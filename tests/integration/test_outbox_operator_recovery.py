from datetime import UTC, datetime, timedelta
from uuid import uuid4
from typing import Any
from hashlib import sha256
from io import BytesIO
import pytest
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.audit.service import AuditEventService
from app.documents.models import (
    DocumentIngestionIntent as Intent,
    DocumentVersion as Version,
    DocumentProcessingStatus as Status,
)
from app.documents.recovery import rearm_intent
from app.documents.relay import IngestionRelay
from app.documents.storage import InMemoryDocumentStorage
from app.identity.models import OrganizationMember
from app.infrastructure.database import build_session_factory
from tests.integration.test_ingestion_outbox import persist
from tests.integration.test_document_ingestion import ingestion_service

pytestmark = pytest.mark.integration


def exhausted(engine: Engine):
    org, version_id, intent_id, task_id = persist(engine)
    with Session(engine) as session, session.begin():
        intent = session.get(Intent, intent_id)
        assert intent
        intent.unresolved_at = datetime.now(UTC)
        intent.last_error = "attempts_exhausted"
        intent.publish_attempts = 20
        intent.recovery_attempts = 3
        intent.published_at = datetime.now(UTC) - timedelta(hours=1)
        actor = session.scalar(
            select(OrganizationMember.user_id).where(
                OrganizationMember.organization_id == org
            )
        )
    return org, version_id, intent_id, task_id, actor


def rearm(engine, ids, **overrides):
    values: dict[str, Any] = dict(
        organization_id=ids[0],
        intent_id=ids[2],
        actor_user_id=ids[4],
        verify_dependencies=lambda: True,
        extracting_seconds=375,
        max_worker_attempts=4,
    )
    values.update(overrides)
    return rearm_intent(build_session_factory(engine), **values)


def test_rearm_is_single_audited_grant_not_counter_reset(database_engine: Engine):
    ids = exhausted(database_engine)
    assert rearm(database_engine, ids) == 1
    with Session(database_engine) as session:
        intent = session.get(Intent, ids[2])
        assert intent and intent.task_id == ids[3] and intent.unresolved_at is None
        assert intent.publish_attempts == 20 and intent.recovery_attempts == 3
        audit = session.scalar(
            select(AuditEvent).where(AuditEvent.action == "document.dispatch_rearmed")
        )
        assert (
            audit and audit.actor_user_id == ids[4] and audit.organization_id == ids[0]
        )
        assert audit.event_metadata == {
            "reason": "dependency_repaired",
            "grant_number": 1,
        }
    relay = IngestionRelay(build_session_factory(database_engine), lambda _: None)
    job = relay.claim()
    assert job and job.task_id == ids[3]
    storage = InMemoryDocumentStorage()
    with Session(database_engine) as session, session.begin():
        version = session.get(Version, ids[1])
        assert version
        version.content_sha256 = sha256(b"safe").hexdigest()
        key = version.storage_key
    storage.put(key, BytesIO(b"safe"))
    service = ingestion_service(database_engine, storage)
    with service.delivery_lock(ids[1]) as acquired:
        assert acquired
        service.process(ids[1], job.task_id)
    assert relay.finish(job, success=True)
    assert relay.settle_terminal() == 1
    with Session(database_engine) as session:
        version = session.get(Version, ids[1])
        assert version and version.status == Status.READY and version.attempt_count == 1
    with pytest.raises(ValueError):
        rearm(database_engine, ids)


@pytest.mark.parametrize(
    "case",
    [
        "lease",
        "ambiguous",
        "active_extracting",
        "budget",
        "grants",
        "wrong_tenant",
        "wrong_actor",
        "dependency",
    ],
)
def test_rearm_rejects_unsafe_cases(database_engine: Engine, case):
    ids = exhausted(database_engine)
    with Session(database_engine) as session, session.begin():
        intent, version = session.get(Intent, ids[2]), session.get(Version, ids[1])
        assert intent and version
        if case == "lease":
            intent.lease_token = uuid4()
            intent.lease_expires_at = datetime.now(UTC) + timedelta(seconds=30)
        if case == "ambiguous":
            version.processing_task_id = "different-owner"
        if case == "active_extracting":
            version.status = Status.EXTRACTING
            version.processing_task_id = ids[3]
            version.processing_started_at = datetime.now(UTC)
        if case == "budget":
            version.attempt_count = 4
        if case == "grants":
            intent.recovery_grants = 3
    overrides = {}
    if case == "wrong_tenant":
        overrides["organization_id"] = uuid4()
    if case == "wrong_actor":
        overrides["actor_user_id"] = uuid4()
    if case == "dependency":
        overrides["verify_dependencies"] = lambda: False
    with pytest.raises(ValueError):
        rearm(database_engine, ids, **overrides)
    with Session(database_engine) as session:
        intent = session.get(Intent, ids[2])
        assert intent and intent.unresolved_at


def test_rearm_cannot_overlap_live_worker_lock(database_engine: Engine):
    ids = exhausted(database_engine)
    service = ingestion_service(database_engine, InMemoryDocumentStorage())
    with service.delivery_lock(ids[1]) as acquired:
        assert acquired
        with pytest.raises(ValueError, match="Active worker"):
            rearm(database_engine, ids)


def test_rearm_audit_failure_rolls_back_after_flush(
    database_engine: Engine, monkeypatch
):
    ids = exhausted(database_engine)
    original = AuditEventService.record_document_dispatch_rearmed

    def fail(service, **kwargs):
        result = original(service, **kwargs)
        # Repository session is intentionally reused: force SQL before failure.
        from sqlalchemy.orm import object_session

        session = object_session(result)
        assert session
        session.flush()
        raise RuntimeError("post-flush failure")

    monkeypatch.setattr(AuditEventService, "record_document_dispatch_rearmed", fail)
    with pytest.raises(RuntimeError):
        rearm(database_engine, ids)
    with Session(database_engine) as session:
        intent = session.get(Intent, ids[2])
        assert intent and intent.unresolved_at and intent.recovery_grants == 0
        assert (
            session.scalar(
                select(AuditEvent).where(
                    AuditEvent.action == "document.dispatch_rearmed"
                )
            )
            is None
        )


@pytest.mark.parametrize("terminal", [Status.READY, Status.FAILED])
def test_late_terminal_completion_resolves_exhausted_intent(
    database_engine: Engine, terminal
):
    ids = exhausted(database_engine)
    with Session(database_engine) as session, session.begin():
        version = session.get(Version, ids[1])
        assert version
        version.status = terminal
    relay = IngestionRelay(
        build_session_factory(database_engine),
        lambda _: pytest.fail("terminal must not republish"),
    )
    assert relay.settle_terminal() == 1
    assert relay.settle_terminal() == 0
    with Session(database_engine) as session:
        intent = session.get(Intent, ids[2])
        assert intent and intent.settled_at and intent.unresolved_at is None
        assert intent.recovery_grants == 0 and intent.publish_attempts == 20
    assert relay.cleanup() == 0  # seven-day retention begins at settlement
