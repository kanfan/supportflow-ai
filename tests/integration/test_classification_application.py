from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.classification.application import (
    ApplicationClassificationError,
    ClassificationApplication,
    FakeClassificationRuntime,
)
from app.classification.errors import (
    InvalidProviderOutputError,
    ProviderAuthenticationError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.classification.models import ClassificationOperation
from app.classification.providers import FakeClassificationProvider
from app.config import Settings
from app.identity.models import (
    MembershipRole,
    MembershipStatus,
    Organization,
    OrganizationMember,
    OrganizationStatus,
    User,
    UserStatus,
)
from app.main import create_app
from app.tickets.models import MessageAuthorType, Ticket, TicketMessage
from app.tickets.service import TicketService

pytestmark = pytest.mark.integration
RESPONSE = '{"outcome":"classified","category":"technical_issue"}'


def seed(engine: Engine, *, opening: bool = True) -> tuple[UUID, UUID, UUID]:
    with Session(engine) as session:
        org = Organization(name="Classification test", slug=uuid4().hex)
        user = User(email=f"{uuid4().hex}@example.com", password_hash="test-only")
        session.add(
            OrganizationMember(organization=org, user=user, role=MembershipRole.ADMIN)
        )
        session.flush()
        ticket = Ticket(organization_id=org.id, subject="Synthetic export failure")
        session.add(ticket)
        session.flush()
        if opening:
            session.add(
                TicketMessage(
                    organization_id=org.id,
                    ticket_id=ticket.id,
                    author_type=MessageAuthorType.AGENT,
                    author_user_id=user.id,
                    body="Synthetic: my export fails",
                )
            )
        ids = org.id, user.id, ticket.id
        session.commit()
        return ids


@dataclass
class Harness:
    client: TestClient
    app: FastAPI
    engine: Engine
    ids: tuple[UUID, UUID, UUID]
    fake: FakeClassificationProvider

    @property
    def url(self) -> str:
        return f"/api/v1/tickets/{self.ids[2]}/classification"

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.app.state.access_token_manager.issue(self.ids[1]).value}",
            "X-Organization-ID": str(self.ids[0]),
        }

    @property
    def service(self) -> ClassificationApplication:
        return ClassificationApplication(
            self.app.state.session_factory, self.app.state.classification_runtime
        )


@pytest.fixture
def harness(database_engine: Engine, migrated_database_url: str) -> Iterator[Harness]:
    fake = FakeClassificationProvider("local", {"local": RESPONSE})
    app = create_app(
        Settings(environment="test", database_url=SecretStr(migrated_database_url)),
        classification_runtime=FakeClassificationRuntime(fake),
    )
    with TestClient(app) as client:
        yield Harness(client, app, database_engine, seed(database_engine), fake)


def test_api_success_reuse_and_safe_audit(harness: Harness) -> None:
    h = harness
    assert h.client.get(h.url, headers=h.headers).json()["state"] == "not_requested"
    first = h.client.post(h.url, headers=h.headers)
    assert first.status_code == 200, first.text
    assert first.json()["result"] == {
        "outcome": "classified",
        "category": "technical_issue",
    }
    assert first.json()["mode"] == "fake"
    assert first.json()["state"] == "current"
    assert h.client.post(h.url, headers=h.headers).json() == first.json()
    assert h.client.get(h.url, headers=h.headers).json() == first.json()
    assert h.fake.calls == 1
    with Session(h.engine) as session:
        row = session.scalar(select(ClassificationOperation))
        assert row is not None and row.requesting_actor_user_id == h.ids[1]
        assert row.attempt_count == 1 and row.elapsed_ms is not None
        assert row.input_tokens is row.output_tokens is row.cost is None
        event = session.scalar(
            select(AuditEvent).where(AuditEvent.action == "classification.finished")
        )
        assert event is not None
        assert event.event_metadata == {"mode": "fake", "state": "succeeded"}
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 1
    assert "Synthetic" not in first.text and "fingerprint" not in first.text


def test_api_auth_cross_tenant_and_disabled(harness: Harness) -> None:
    h = harness
    assert h.client.post(h.url).status_code == 401
    other = seed(h.engine)
    for method in (h.client.get, h.client.post):
        assert (
            method(
                f"/api/v1/tickets/{other[2]}/classification", headers=h.headers
            ).status_code
            == 404
        )
        assert (
            method(
                f"/api/v1/tickets/{uuid4()}/classification", headers=h.headers
            ).status_code
            == 404
        )
        headers = dict(h.headers, **{"X-Organization-ID": str(other[0])})
        assert method(h.url, headers=headers).status_code == 404
    assert (
        h.client.post(
            h.url, headers=h.headers, json={"subject": "override"}
        ).status_code
        == 422
    )
    h.app.state.classification_runtime = None
    response = h.client.post(h.url, headers=h.headers)
    assert (
        response.status_code == 503
        and response.json()["error"]["code"] == "classification_disabled"
    )
    assert h.fake.calls == 0


@pytest.mark.parametrize("target", ["member", "user", "organization"])
def test_inactive_context_blocks_both_routes(harness: Harness, target: str) -> None:
    h = harness
    headers = h.headers
    with Session(h.engine) as session:
        if target == "member":
            row = session.get(OrganizationMember, (h.ids[0], h.ids[1]))
            assert row
            row.status = MembershipStatus.INACTIVE
        elif target == "user":
            row = session.get(User, h.ids[1])
            assert row
            row.status = UserStatus.DISABLED
        else:
            row = session.get(Organization, h.ids[0])
            assert row
            row.status = OrganizationStatus.SUSPENDED
        session.commit()
    for method in (h.client.get, h.client.post):
        assert method(h.url, headers=headers).status_code in (401, 404)
    assert h.fake.calls == 0


def test_agent_role_is_allowed(harness: Harness) -> None:
    with Session(harness.engine) as session:
        row = session.get(OrganizationMember, harness.ids[:2])
        assert row
        row.role = MembershipRole.AGENT
        session.commit()
    assert harness.client.post(harness.url, headers=harness.headers).status_code == 200


def test_fk_rejects_other_tenant_actor_and_ticket(harness: Harness) -> None:
    h = harness
    h.service.classify(*h.ids)
    other = seed(h.engine)
    with Session(h.engine) as session:
        row = session.scalar(select(ClassificationOperation))
        assert row
        values = {
            column.name: getattr(row, column.name)
            for column in ClassificationOperation.__table__.columns
        }
    for changes in ({"requesting_actor_user_id": other[1]}, {"ticket_id": other[2]}):
        with Session(h.engine) as session:
            with pytest.raises(IntegrityError):
                session.execute(
                    insert(ClassificationOperation).values(
                        **dict(values, id=uuid4(), config_digest=uuid4().hex, **changes)
                    )
                )
                session.commit()


@pytest.mark.parametrize(
    "changes",
    [
        {"outcome": None},
        {"category": None},
        {"outcome": "insufficient_context"},
        {"state": "failed"},
        {"mode": "real"},
        {"attempt_count": 2},
        {"input_tokens": 10},
    ],
)
def test_database_rejects_invalid_result_pairs(harness: Harness, changes: dict) -> None:
    harness.service.classify(*harness.ids)
    with Session(harness.engine) as session:
        with pytest.raises(IntegrityError):
            session.execute(update(ClassificationOperation).values(**changes))
            session.commit()


def test_local_missing_opening_shortcut_and_append_freshness(harness: Harness) -> None:
    h = harness
    ids = seed(h.engine, opening=False)
    service = h.service
    first = service.classify(*ids)
    assert first.result and first.result.outcome == "insufficient_context"
    assert h.fake.calls == 0
    with Session(h.engine) as session:
        TicketService(session, ids[0]).append_agent_message(
            ticket_id=ids[2], body="Synthetic first request", current_user_id=ids[1]
        )
    assert service.read(*ids).state == "stale"
    assert service.classify(*ids).state == "current" and h.fake.calls == 1


def test_followup_does_not_invalidate_but_subject_and_config_do(
    harness: Harness,
) -> None:
    h = harness
    first = h.service.classify(*h.ids)
    with Session(h.engine) as session:
        TicketService(session, h.ids[0]).append_agent_message(
            ticket_id=h.ids[2], body="Later followup", current_user_id=h.ids[1]
        )
    assert h.service.read(*h.ids) == first
    with Session(h.engine) as session:
        ticket = session.scalar(
            select(Ticket).where(Ticket.id == h.ids[2]).with_for_update()
        )
        assert ticket
        ticket.subject = "Changed synthetic subject"
        session.commit()
    assert h.service.read(*h.ids).state == "stale"
    second = h.service.classify(*h.ids)
    assert second.result_id != first.result_id
    h.app.state.classification_runtime = FakeClassificationRuntime(
        h.fake, "fake_application.v2"
    )
    assert h.service.read(*h.ids).state == "stale"
    assert h.service.classify(*h.ids).result_id != second.result_id
    assert h.fake.calls == 3


def test_changed_opening_body_invalidates(harness: Harness) -> None:
    h = harness
    h.service.classify(*h.ids)
    with Session(h.engine) as session:
        session.scalar(select(Ticket).where(Ticket.id == h.ids[2]).with_for_update())
        session.execute(
            update(TicketMessage)
            .where(TicketMessage.ticket_id == h.ids[2])
            .values(body="Changed opening")
        )
        session.commit()
    view = h.service.read(*h.ids)
    assert view.state == "stale" and view.result is None


@pytest.mark.parametrize(
    ("script", "status", "state"),
    [
        (ProviderTimeoutError, 504, "unknown"),
        (ProviderUnavailableError, 503, "unknown"),
        (ProviderAuthenticationError, 503, "failed"),
        (ProviderRateLimitedError, 503, "failed"),
        (InvalidProviderOutputError, 502, "failed"),
        ("private invalid response", 502, "failed"),
    ],
)
def test_errors_are_safe_terminal_and_never_retried(
    harness: Harness, script, status: int, state: str
) -> None:
    h = harness
    fake = FakeClassificationProvider("error", {"error": script})
    h.app.state.classification_runtime = FakeClassificationRuntime(fake)
    for _ in range(2):
        response = h.client.post(h.url, headers=h.headers)
        assert response.status_code == status
        assert "private invalid" not in response.text
    assert fake.calls == 1
    view = h.client.get(h.url, headers=h.headers).json()
    assert view["state"] == state and view["result"] is None


def test_oversized_input_does_not_claim_or_call(harness: Harness) -> None:
    h = harness
    with Session(h.engine) as session:
        session.execute(
            update(TicketMessage)
            .where(TicketMessage.ticket_id == h.ids[2])
            .values(body="x" * 8001)
        )
        session.commit()
    assert h.client.post(h.url, headers=h.headers).status_code == 422
    assert h.fake.calls == 0
    with Session(h.engine) as session:
        assert (
            session.scalar(select(func.count()).select_from(ClassificationOperation))
            == 0
        )


def test_io_releases_auth_and_service_connections(
    harness: Harness, monkeypatch
) -> None:
    h = harness
    original = h.fake.classify

    def checked(payload):
        engine = h.app.state.session_factory.kw["bind"]
        assert engine.pool.checkedout() == 0
        with h.engine.connect() as connection:
            # Also prove no conflicting lock from the claim transaction remains.
            connection.execute(text("SET LOCAL lock_timeout = '500ms'"))
            connection.execute(
                select(Ticket).where(Ticket.id == h.ids[2]).with_for_update(nowait=True)
            )
        return original(payload)

    monkeypatch.setattr(h.fake, "classify", checked)
    response = h.client.post(h.url, headers=h.headers)
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("mutation", ["input", "membership"])
def test_completion_rechecks_input_and_access(
    harness: Harness, monkeypatch, mutation: str
) -> None:
    h = harness
    original = h.fake.classify

    def mutate(payload):
        with Session(h.engine) as session:
            if mutation == "input":
                ticket = session.scalar(
                    select(Ticket).where(Ticket.id == h.ids[2]).with_for_update()
                )
                assert ticket
                ticket.subject = "New opening subject"
            else:
                member = session.get(OrganizationMember, h.ids[:2])
                assert member
                member.status = MembershipStatus.INACTIVE
            session.commit()
        return original(payload)

    monkeypatch.setattr(h.fake, "classify", mutate)
    response = h.client.post(h.url, headers=h.headers)
    assert response.status_code == (409 if mutation == "input" else 403)
    with Session(h.engine) as session:
        row = session.scalar(select(ClassificationOperation))
        assert row and row.outcome is None and row.category is None
        assert row.state == ("stale" if mutation == "input" else "failed")


def test_concurrent_duplicate_and_expired_completion_do_not_call_again(
    harness: Harness, monkeypatch
) -> None:
    h = harness
    entered, release = Event(), Event()
    original = h.fake.classify

    def blocked(payload):
        entered.set()
        assert release.wait(10)
        return original(payload)

    monkeypatch.setattr(h.fake, "classify", blocked)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(h.service.classify, *h.ids)
        try:
            assert entered.wait(5)
            assert h.service.read(*h.ids).state == "in_progress"
            with pytest.raises(ApplicationClassificationError) as duplicate:
                h.service.classify(*h.ids)
            assert duplicate.value.code == "classification_in_progress"
            with Session(h.engine) as session:
                session.execute(
                    update(ClassificationOperation).values(
                        expires_at=datetime.now(UTC) - timedelta(seconds=1)
                    )
                )
                session.commit()
            assert h.service.read(*h.ids).state == "unknown"
            # GET is read-only: the expired row has not been rewritten.
            with Session(h.engine) as session:
                assert (
                    session.scalar(select(ClassificationOperation.state))
                    == "in_progress"
                )
            with pytest.raises(ApplicationClassificationError) as expired:
                h.service.classify(*h.ids)
            assert expired.value.code == "classification_unknown"
        finally:
            release.set()
        with pytest.raises(ApplicationClassificationError):
            first.result(timeout=5)
    assert h.fake.calls == 1
    assert h.service.read(*h.ids).state == "unknown"


def test_append_participates_in_ticket_lock(harness: Harness) -> None:
    h = harness
    with Session(h.engine) as lock_session:
        lock_session.scalar(
            select(Ticket).where(Ticket.id == h.ids[2]).with_for_update()
        )
        with Session(h.engine) as writer:
            writer.execute(text("SET LOCAL lock_timeout = '100ms'"))
            with pytest.raises(DBAPIError):
                TicketService(writer, h.ids[0]).append_agent_message(
                    ticket_id=h.ids[2], body="Must wait", current_user_id=h.ids[1]
                )


@pytest.mark.parametrize(
    "terminal_error", [False, True], ids=["success", "terminal-error"]
)
def test_audit_failure_rolls_back_completion_leaves_unrepeatable_claim(
    harness: Harness, monkeypatch, terminal_error: bool
) -> None:
    h = harness
    if terminal_error:
        h.fake = FakeClassificationProvider(
            "error", {"error": ProviderAuthenticationError}
        )
        h.app.state.classification_runtime = FakeClassificationRuntime(h.fake)
    service = h.service
    real_audit = service._audit
    flushed = []

    def fail_after_flush(session: Session, operation: ClassificationOperation) -> None:
        real_audit(session, operation)
        session.flush()
        # Select columns, not ORM objects: prove both writes reached PostgreSQL,
        # rather than inspecting the identity map's pending in-memory values.
        row = session.execute(
            select(
                ClassificationOperation.state,
                ClassificationOperation.outcome,
                ClassificationOperation.category,
                ClassificationOperation.error_code,
                ClassificationOperation.completed_at,
            ).where(ClassificationOperation.id == operation.id)
        ).one()
        assert row.state == ("failed" if terminal_error else "succeeded")
        assert row.outcome == (None if terminal_error else "classified")
        assert row.category == (None if terminal_error else "technical_issue")
        assert row.error_code == ("provider_authentication" if terminal_error else None)
        assert row.completed_at is not None
        metadata = session.scalar(
            select(AuditEvent.event_metadata).where(
                AuditEvent.resource_id == operation.id,
                AuditEvent.action == "classification.finished",
            )
        )
        assert metadata == {"mode": "fake", "state": row.state}
        flushed.append(operation.id)
        raise RuntimeError("audit unavailable after flush")

    monkeypatch.setattr(service, "_audit", fail_after_flush)
    with pytest.raises(RuntimeError, match="audit unavailable after flush"):
        service.classify(*h.ids)
    assert len(flushed) == 1
    with Session(h.engine) as session:
        row = session.get(ClassificationOperation, flushed[0])
        assert row and row.state == "in_progress" and row.outcome is None
        assert (
            row.category is row.error_code is row.completed_at is row.elapsed_ms is None
        )
        assert row.attempt_count == 1
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 0
    with pytest.raises(ApplicationClassificationError) as duplicate:
        h.service.classify(*h.ids)
    assert duplicate.value.code == "classification_in_progress"
    assert h.fake.calls == 1


def test_error_to_equal_response_script_change_is_stale_and_gets_new_claim(
    harness: Harness,
) -> None:
    h = harness
    error = FakeClassificationProvider("error", {"error": ProviderUnavailableError})
    response = FakeClassificationProvider(
        "response", {"response": "provider_unavailable"}
    )
    h.app.state.classification_runtime = FakeClassificationRuntime(error)
    first = h.client.post(h.url, headers=h.headers)
    assert first.status_code == 503
    assert first.json()["error"]["code"] == "provider_unavailable"
    old = h.client.get(h.url, headers=h.headers).json()
    assert old["state"] == "unknown"

    # Same configuration_version: the changed script alone must change identity.
    h.app.state.classification_runtime = FakeClassificationRuntime(response)
    stale = h.client.get(h.url, headers=h.headers).json()
    assert stale["state"] == "stale" and stale["result_id"] == old["result_id"]
    second = h.client.post(h.url, headers=h.headers)
    assert second.status_code == 502
    assert second.json()["error"]["code"] == "invalid_provider_output"
    new = h.client.get(h.url, headers=h.headers).json()
    assert new["state"] == "failed" and new["result_id"] != old["result_id"]
    assert h.client.post(h.url, headers=h.headers).status_code == 502
    assert error.calls == response.calls == 1
    with Session(h.engine) as session:
        rows = session.scalars(select(ClassificationOperation)).all()
        assert len(rows) == 2
        assert {row.state for row in rows} == {"unknown", "failed"}
        assert len({row.config_digest for row in rows}) == 2
        assert len({row.input_fingerprint for row in rows}) == 1


def test_unknown_exception_is_sanitized(harness: Harness, monkeypatch) -> None:
    def fail(_payload):
        raise RuntimeError("secret-provider-response")

    monkeypatch.setattr(harness.fake, "classify", fail)
    response = harness.client.post(harness.url, headers=harness.headers)
    assert response.status_code == 503
    assert "secret-provider-response" not in response.text
    assert harness.service.read(*harness.ids).state == "unknown"


def test_downgrade_preserves_classification_evidence(
    harness: Harness, migrated_database_url: str
) -> None:
    harness.service.classify(*harness.ids)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", migrated_database_url.replace("%", "%%"))
    with pytest.raises(DBAPIError, match="Classification evidence remains"):
        command.downgrade(config, "0006_outbox_lifecycle")
    with Session(harness.engine) as session:
        assert (
            session.scalar(text("SELECT version_num FROM alembic_version"))
            == "0007_classification"
        )
        assert (
            session.scalar(select(func.count()).select_from(ClassificationOperation))
            == 1
        )
