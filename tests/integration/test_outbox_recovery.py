from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.documents.models import (
    DocumentIngestionIntent as Intent,
    DocumentVersion as Version,
    DocumentProcessingStatus as Status,
)
from app.documents.relay import IngestionRelay, RelayPolicy, backfill
from app.documents.storage import InMemoryDocumentStorage
from app.infrastructure.database import build_session_factory
from tests.integration.test_ingestion_outbox import persist, tenant, upload
from tests.integration.test_document_ingestion import (
    create_queued_version,
    ingestion_service,
)

pytestmark = pytest.mark.integration


def due(engine: Engine, intent_id: UUID) -> None:
    with Session(engine) as session, session.begin():
        intent = session.get(Intent, intent_id)
        assert intent
        intent.available_at = datetime.now(UTC) - timedelta(seconds=1)
        if intent.lease_expires_at:
            intent.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)


def test_two_relays_and_expired_owner_are_fenced(database_engine: Engine):
    _, _, intent_id, task_id = persist(database_engine)
    relay = IngestionRelay(build_session_factory(database_engine), lambda _: None)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: relay.claim(), range(2)))
    jobs = [job for job in claims if job]
    assert len(jobs) == 1
    first = jobs[0]
    due(database_engine, intent_id)
    second = relay.claim()
    assert second and second.task_id == first.task_id == task_id
    assert second.token != first.token
    assert relay.finish(first, success=True) is False
    assert relay.finish(second, success=True) is True


def test_publication_failure_retains_intent_and_bounded_exhaustion(
    database_engine: Engine,
):
    _, version_id, intent_id, _ = persist(database_engine)

    def fail(_):
        raise RuntimeError("secret broker URI must not be persisted")

    relay = IngestionRelay(
        build_session_factory(database_engine), fail, RelayPolicy(max_publications=2)
    )
    for _ in range(3):
        due(database_engine, intent_id)
        relay.tick()
    with Session(database_engine) as session:
        intent, version = (
            session.get(Intent, intent_id),
            session.get(Version, version_id),
        )
        assert intent and version
        assert intent.unresolved_at and intent.last_error == "attempts_exhausted"
        assert intent.publish_attempts == 2 and intent.published_at is None
        assert version.status == Status.QUEUED
    assert relay.cleanup() == 0


def test_published_does_not_mean_processed_and_lost_queue_is_republished(
    database_engine: Engine,
):
    _, _, intent_id, task_id = persist(database_engine)
    messages = []
    relay = IngestionRelay(build_session_factory(database_engine), messages.append)
    relay.tick()
    messages.clear()  # accepted broker message lost before any worker starts
    due(database_engine, intent_id)
    relay.tick()
    assert len(messages) == 1 and messages[0].task_id == task_id
    with Session(database_engine) as session:
        intent = session.get(Intent, intent_id)
        assert intent and intent.published_at and intent.settled_at is None
        assert intent.recovery_attempts == 1


def test_crash_after_publish_before_mark_converges_terminal_effects(
    database_engine: Engine,
):
    storage = InMemoryDocumentStorage()
    stored = create_queued_version(database_engine, storage)
    backfill(build_session_factory(database_engine), cutoff=datetime.now(UTC))
    service = ingestion_service(database_engine, storage)

    def publish(job):
        with service.delivery_lock(job.version_id) as acquired:
            assert acquired
            service.process(job.version_id, job.task_id)

    relay = IngestionRelay(build_session_factory(database_engine), publish)
    job = relay.claim()
    assert job
    publish(job)  # process dies before relay.finish
    due(database_engine, job.intent_id)
    assert relay.claim() is None  # DB terminal state wins, no extra publish
    publish(job)  # a broker duplicate also does not add a terminal audit
    with Session(database_engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.resource_id == stored.version_id)
            )
            == 1
        )
        intent = session.get(Intent, job.intent_id)
        assert intent and intent.settled_at


def test_claim_racing_reconciliation_and_active_same_id_lock(database_engine: Engine):
    storage = InMemoryDocumentStorage()
    stored = create_queued_version(database_engine, storage)
    backfill(build_session_factory(database_engine), cutoff=datetime.now(UTC))
    relay = IngestionRelay(build_session_factory(database_engine), lambda _: None)
    job = relay.claim()
    assert job
    service = ingestion_service(database_engine, storage)
    with service.delivery_lock(stored.version_id) as acquired:
        assert acquired
        claimed = service._claim(stored.version_id, job.task_id)
        assert claimed.record
        # Even a message prepared before the worker claim cannot run beside it.
        with service.delivery_lock(stored.version_id) as second:
            assert second is False
    assert relay.finish(job, success=True)
    due(database_engine, job.intent_id)
    assert relay.claim() is None  # extracting deadline not passed
    with Session(database_engine) as session:
        version = session.get(Version, stored.version_id)
        assert (
            version
            and version.processing_task_id == job.task_id
            and version.attempt_count == 1
        )


def test_stale_extracting_reuses_identity_without_resetting_attempts(
    database_engine: Engine,
):
    storage = InMemoryDocumentStorage()
    stored = create_queued_version(database_engine, storage)
    backfill(build_session_factory(database_engine), cutoff=datetime.now(UTC))
    relay = IngestionRelay(build_session_factory(database_engine), lambda _: None)
    first = relay.claim()
    assert first
    service = ingestion_service(database_engine, storage)
    service._claim(stored.version_id, first.task_id)
    relay.finish(first, success=True)
    with Session(database_engine) as session, session.begin():
        version = session.get(Version, stored.version_id)
        assert version
        version.processing_started_at = datetime.now(UTC) - timedelta(hours=1)
    due(database_engine, first.intent_id)
    recovered = relay.claim()
    assert recovered and recovered.task_id == first.task_id and recovered.retries == 0
    with service.delivery_lock(stored.version_id) as acquired:
        assert acquired
        service.process(stored.version_id, recovered.task_id)
    with Session(database_engine) as session:
        version = session.get(Version, stored.version_id)
        assert version and version.status == Status.READY and version.attempt_count == 1


def test_ambiguous_extracting_owner_is_not_stolen(database_engine: Engine):
    _, version_id, intent_id, _ = persist(database_engine)
    with Session(database_engine) as session, session.begin():
        version = session.get(Version, version_id)
        assert version
        version.status = Status.EXTRACTING
        version.processing_task_id = "legacy-different-owner"
        version.processing_started_at = datetime.now(UTC) - timedelta(hours=1)
    relay = IngestionRelay(
        build_session_factory(database_engine),
        lambda _: pytest.fail("must not publish"),
    )
    relay.tick()
    with Session(database_engine) as session:
        intent, version = (
            session.get(Intent, intent_id),
            session.get(Version, version_id),
        )
        assert (
            intent
            and intent.unresolved_at
            and intent.last_error == "ownership_ambiguous"
        )
        assert version and version.processing_task_id == "legacy-different-owner"


def test_queued_republication_preserves_retry_budget(database_engine: Engine):
    _, version_id, intent_id, task_id = persist(database_engine)
    with Session(database_engine) as session, session.begin():
        version = session.get(Version, version_id)
        assert version
        version.attempt_count = 2
    relay = IngestionRelay(build_session_factory(database_engine), lambda _: None)
    job = relay.claim()
    assert job and job.task_id == task_id and job.retries == 2
    assert job.intent_id == intent_id
    with Session(database_engine) as session:
        version = session.get(Version, version_id)
        assert version and version.attempt_count == 2


def test_durable_retry_exhaustion_clears_owner_and_is_terminal_once(
    database_engine: Engine,
):
    _, version_id, _, task_id = persist(database_engine)
    with Session(database_engine) as session, session.begin():
        version = session.get(Version, version_id)
        assert version
        version.attempt_count = 4
        version.processing_task_id = task_id
    service = ingestion_service(database_engine, InMemoryDocumentStorage())
    with service.delivery_lock(version_id) as acquired:
        assert acquired
        service.process(version_id, task_id)
        service.process(version_id, task_id)
    with Session(database_engine) as session:
        version = session.get(Version, version_id)
        assert version and version.status == Status.FAILED
        assert version.attempt_count == 4 and version.processing_task_id is None
        assert version.processing_started_at is None and version.extracted_text is None
        assert version.error_code == "retry_exhausted"
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.resource_id == version_id)
            )
            == 1
        )


def test_backfill_cutoff_idempotency_and_failed_exclusion(database_engine: Engine):
    org, user = tenant(database_engine)
    before, version = upload(org, user)
    version_id = version.id
    with Session(database_engine) as session, session.begin():
        session.add_all([before, version])
    cutoff = datetime.now(UTC)
    after, later = upload(org, user)
    failed_doc, failed = upload(org, user)
    failed.status = Status.FAILED
    with Session(database_engine) as session, session.begin():
        session.add_all([after, later, failed_doc, failed])
    sessions = build_session_factory(database_engine)
    assert backfill(sessions, cutoff=cutoff) == 1
    assert backfill(sessions, cutoff=cutoff) == 0
    with Session(database_engine) as session:
        assert session.scalar(select(Intent.document_version_id)) == version_id


@pytest.mark.parametrize(
    "age,unresolved,leased,expected",
    [
        (6, False, False, 0),
        (8, False, False, 1),
        (8, True, False, 0),
        (8, False, True, 0),
    ],
)
def test_retention_requires_seven_days_and_no_unresolved_or_lease(
    database_engine: Engine, age, unresolved, leased, expected
):
    _, _, intent_id, _ = persist(database_engine)
    with Session(database_engine) as session, session.begin():
        intent = session.get(Intent, intent_id)
        assert intent
        intent.settled_at = datetime.now(UTC) - timedelta(days=age)
        if unresolved:
            intent.unresolved_at = datetime.now(UTC)
        if leased:
            intent.lease_token = intent.id
            intent.lease_expires_at = datetime.now(UTC) + timedelta(seconds=30)
    relay = IngestionRelay(build_session_factory(database_engine), lambda _: None)
    assert relay.cleanup() == expected
