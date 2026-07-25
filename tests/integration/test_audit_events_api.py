from collections.abc import Iterator
from datetime import UTC, datetime
import json
from typing import Any, cast
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest
from pydantic import SecretStr
from sqlalchemy import Table, func, insert, inspect, select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.audit.models import AuditAction, AuditEvent, AuditResourceType
from app.config import Settings
from app.identity.models import OrganizationMember
from app.main import create_app


pytestmark = pytest.mark.integration
TEST_AUTH_SECRET = "audit-test-secret-with-thirty-two-bytes"


@pytest.fixture(scope="module")
def audit_client(migrated_database_url: str) -> Iterator[TestClient]:
    application = create_app(
        Settings(
            environment="test",
            database_url=migrated_database_url,
            auth_secret_key=SecretStr(TEST_AUTH_SECRET),
            auth_issuer="supportflow-audit-integration-test",
            auth_audience="supportflow-api-audit-test",
        )
    )
    with TestClient(application) as client:
        yield client


def registration_payload(prefix: str) -> dict[str, str]:
    return {
        "email": f"{prefix}@example.com",
        "password": "correct horse battery staple",
        "organization_name": f"{prefix} Organization",
        "organization_slug": prefix,
    }


def register(client: TestClient, prefix: str) -> dict[str, Any]:
    response = client.post(
        "/api/v1/auth/register",
        json=registration_payload(prefix),
    )
    assert response.status_code == 201, response.text
    return response.json()


def login(client: TestClient, email: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": "correct horse battery staple",
        },
    )
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


def bearer_headers(token: str, organization_id: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Organization-ID": organization_id,
    }


def add_agent(
    client: TestClient,
    *,
    token: str,
    organization_id: str,
    email: str,
) -> Any:
    return client.post(
        "/api/v1/organization-members",
        headers=bearer_headers(token, organization_id),
        json={"email": email, "role": "agent"},
    )


def test_audit_migration_has_expected_columns_constraints_index_and_trigger(
    database_engine: Engine,
) -> None:
    schema = inspect(database_engine)
    columns = {column["name"] for column in schema.get_columns("audit_events")}
    foreign_keys = schema.get_foreign_keys("audit_events")
    indexes = {index["name"] for index in schema.get_indexes("audit_events")}

    assert columns == {
        "id",
        "organization_id",
        "actor_user_id",
        "action",
        "resource_type",
        "resource_id",
        "metadata",
        "created_at",
    }
    assert {
        tuple(foreign_key["constrained_columns"]) for foreign_key in foreign_keys
    } >= {
        ("organization_id",),
        ("organization_id", "actor_user_id"),
    }
    assert "ix_audit_events_organization_created_id" in indexes

    with database_engine.connect() as connection:
        trigger_names = set(
            connection.exec_driver_sql(
                """
                SELECT tgname
                FROM pg_trigger
                WHERE tgrelid = 'audit_events'::regclass
                  AND NOT tgisinternal
                """
            ).scalars()
        )
    assert trigger_names >= {
        "audit_events_append_only",
        "audit_events_append_only_truncate",
    }


def test_membership_and_audit_event_commit_together_with_safe_metadata(
    audit_client: TestClient,
    database_engine: Engine,
) -> None:
    admin = register(audit_client, f"audit-admin-{uuid4().hex}")
    target = register(audit_client, f"audit-target-{uuid4().hex}")
    token = login(audit_client, admin["user"]["email"])

    response = add_agent(
        audit_client,
        token=token,
        organization_id=admin["organization"]["id"],
        email=target["user"]["email"],
    )

    assert response.status_code == 201
    with Session(database_engine) as session:
        membership = session.get(
            OrganizationMember,
            (admin["organization"]["id"], target["user"]["id"]),
        )
        events = list(
            session.scalars(
                select(AuditEvent).where(
                    AuditEvent.organization_id == admin["organization"]["id"]
                )
            )
        )

    assert membership is not None
    assert len(events) == 1
    event = events[0]
    assert event.actor_user_id == UUID(admin["user"]["id"])
    assert event.action == AuditAction.ORGANIZATION_MEMBER_CREATED
    assert event.resource_type == AuditResourceType.ORGANIZATION_MEMBER
    assert event.resource_id == UUID(target["user"]["id"])
    assert event.event_metadata == {"role": "agent"}

    serialized_metadata = json.dumps(event.event_metadata).lower()
    for forbidden_value in (
        "password",
        "authorization",
        "bearer",
        "token",
        admin["user"]["email"],
        target["user"]["email"],
    ):
        assert forbidden_value not in serialized_metadata


def test_failure_after_database_flush_rolls_back_membership_and_event(
    audit_client: TestClient,
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = register(audit_client, f"rollback-admin-{uuid4().hex}")
    target = register(audit_client, f"rollback-target-{uuid4().hex}")
    token = login(audit_client, admin["user"]["email"])
    organization_id = UUID(admin["organization"]["id"])
    target_user_id = UUID(target["user"]["id"])
    flushed_to_database = False

    def flush_then_fail(session: Session) -> None:
        nonlocal flushed_to_database
        session.flush()
        membership_count = session.scalar(
            select(func.count())
            .select_from(OrganizationMember)
            .where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.user_id == target_user_id,
            )
        )
        audit_count = session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.organization_id == organization_id,
                AuditEvent.resource_id == target_user_id,
            )
        )
        assert membership_count == 1
        assert audit_count == 1
        flushed_to_database = True
        raise RuntimeError("forced failure after database flush")

    monkeypatch.setattr(Session, "commit", flush_then_fail)

    with pytest.raises(RuntimeError, match="forced failure after database flush"):
        add_agent(
            audit_client,
            token=token,
            organization_id=str(organization_id),
            email=target["user"]["email"],
        )

    assert flushed_to_database is True
    with Session(database_engine) as verification_session:
        membership_count = verification_session.scalar(
            select(func.count())
            .select_from(OrganizationMember)
            .where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.user_id == target_user_id,
            )
        )
        audit_count = verification_session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.organization_id == organization_id,
                AuditEvent.resource_id == target_user_id,
            )
        )
    assert membership_count == 0
    assert audit_count == 0


def test_admin_list_is_tenant_scoped_and_agent_is_forbidden(
    audit_client: TestClient,
    database_engine: Engine,
) -> None:
    first_admin = register(audit_client, f"tenant-a-admin-{uuid4().hex}")
    second_admin = register(audit_client, f"tenant-b-admin-{uuid4().hex}")
    first_agent = register(audit_client, f"tenant-a-agent-{uuid4().hex}")
    second_agent = register(audit_client, f"tenant-b-agent-{uuid4().hex}")
    first_admin_token = login(audit_client, first_admin["user"]["email"])
    second_admin_token = login(audit_client, second_admin["user"]["email"])
    first_agent_token = login(audit_client, first_agent["user"]["email"])

    assert (
        add_agent(
            audit_client,
            token=first_admin_token,
            organization_id=first_admin["organization"]["id"],
            email=first_agent["user"]["email"],
        ).status_code
        == 201
    )
    assert (
        add_agent(
            audit_client,
            token=second_admin_token,
            organization_id=second_admin["organization"]["id"],
            email=second_agent["user"]["email"],
        ).status_code
        == 201
    )

    first_tenant_list = audit_client.get(
        "/api/v1/audit-events",
        headers=bearer_headers(
            first_admin_token,
            first_admin["organization"]["id"],
        ),
    )
    agent_attempt = audit_client.get(
        "/api/v1/audit-events",
        headers=bearer_headers(
            first_agent_token,
            first_admin["organization"]["id"],
        ),
    )
    cross_tenant_attempt = audit_client.get(
        "/api/v1/audit-events",
        headers=bearer_headers(
            first_admin_token,
            second_admin["organization"]["id"],
        ),
    )

    assert first_tenant_list.status_code == 200
    assert first_tenant_list.json()["pagination"]["total"] == 1
    assert {event["resource_id"] for event in first_tenant_list.json()["items"]} == {
        first_agent["user"]["id"]
    }
    assert agent_attempt.status_code == 403
    assert agent_attempt.json()["error"]["code"] == "forbidden"
    assert cross_tenant_attempt.status_code == 404

    with Session(database_engine) as session:
        all_events = list(session.scalars(select(AuditEvent)))
    assert len(all_events) == 2


@pytest.mark.parametrize("authorization", [None, "Bearer invalid-token"])
def test_audit_endpoint_requires_valid_authentication(
    audit_client: TestClient,
    authorization: str | None,
) -> None:
    headers = {"X-Organization-ID": str(uuid4())}
    if authorization is not None:
        headers["Authorization"] = authorization

    response = audit_client.get("/api/v1/audit-events", headers=headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "unauthorized"


def test_audit_list_has_deterministic_pagination(
    audit_client: TestClient,
    database_engine: Engine,
) -> None:
    admin = register(audit_client, f"page-admin-{uuid4().hex}")
    token = login(audit_client, admin["user"]["email"])
    organization_id = UUID(admin["organization"]["id"])
    actor_user_id = UUID(admin["user"]["id"])
    event_ids = [uuid4() for _ in range(4)]
    event_times = [
        datetime(2030, 1, 1, tzinfo=UTC),
        datetime(2029, 1, 1, tzinfo=UTC),
        datetime(2029, 1, 1, tzinfo=UTC),
        datetime(2025, 1, 1, tzinfo=UTC),
    ]

    with Session(database_engine) as session:
        session.add_all(
            [
                AuditEvent(
                    id=event_id,
                    organization_id=organization_id,
                    actor_user_id=actor_user_id,
                    action=AuditAction.TICKET_MESSAGE_CREATED.value,
                    resource_type=AuditResourceType.TICKET_MESSAGE.value,
                    resource_id=uuid4(),
                    event_metadata={},
                    created_at=created_at,
                )
                for event_id, created_at in zip(
                    event_ids,
                    event_times,
                    strict=True,
                )
            ]
        )
        session.commit()

    first_page = audit_client.get(
        "/api/v1/audit-events",
        params={"limit": 3, "offset": 0},
        headers=bearer_headers(token, str(organization_id)),
    )
    second_page = audit_client.get(
        "/api/v1/audit-events",
        params={"limit": 2, "offset": 3},
        headers=bearer_headers(token, str(organization_id)),
    )

    assert first_page.status_code == 200
    assert [UUID(item["id"]) for item in first_page.json()["items"]] == [
        event_ids[0],
        *sorted(event_ids[1:3], reverse=True),
    ]
    assert first_page.json()["pagination"] == {
        "limit": 3,
        "offset": 0,
        "total": 4,
    }
    assert second_page.status_code == 200
    assert [UUID(item["id"]) for item in second_page.json()["items"]] == [event_ids[3]]
    assert second_page.json()["pagination"] == {
        "limit": 2,
        "offset": 3,
        "total": 4,
    }


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": 101}, {"offset": -1}],
)
def test_audit_list_rejects_invalid_pagination(
    audit_client: TestClient,
    params: dict[str, int],
) -> None:
    admin = register(audit_client, f"bounds-admin-{uuid4().hex}")
    token = login(audit_client, admin["user"]["email"])

    response = audit_client.get(
        "/api/v1/audit-events",
        params=params,
        headers=bearer_headers(token, admin["organization"]["id"]),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_database_rejects_cross_tenant_actor_and_non_object_metadata(
    audit_client: TestClient,
    database_engine: Engine,
) -> None:
    first_admin = register(audit_client, f"fk-a-admin-{uuid4().hex}")
    second_admin = register(audit_client, f"fk-b-admin-{uuid4().hex}")
    audit_events_table = cast(Table, AuditEvent.__table__)

    with Session(database_engine) as session:
        session.add(
            AuditEvent(
                organization_id=first_admin["organization"]["id"],
                actor_user_id=second_admin["user"]["id"],
                action=AuditAction.TICKET_CREATED.value,
                resource_type=AuditResourceType.TICKET.value,
                resource_id=uuid4(),
                event_metadata={"source_type": "manual"},
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        with pytest.raises(IntegrityError):
            session.execute(
                insert(audit_events_table).values(
                    organization_id=first_admin["organization"]["id"],
                    actor_user_id=first_admin["user"]["id"],
                    action=AuditAction.TICKET_CREATED.value,
                    resource_type=AuditResourceType.TICKET.value,
                    resource_id=uuid4(),
                    metadata=["not", "an", "object"],
                )
            )
            session.commit()
        session.rollback()


def test_audit_rows_are_database_enforced_append_only(
    audit_client: TestClient,
    database_engine: Engine,
) -> None:
    admin = register(audit_client, f"append-admin-{uuid4().hex}")
    target = register(audit_client, f"append-target-{uuid4().hex}")
    token = login(audit_client, admin["user"]["email"])
    assert (
        add_agent(
            audit_client,
            token=token,
            organization_id=admin["organization"]["id"],
            email=target["user"]["email"],
        ).status_code
        == 201
    )

    with Session(database_engine) as session:
        event = session.scalar(
            select(AuditEvent).where(
                AuditEvent.organization_id == admin["organization"]["id"]
            )
        )
        assert event is not None
        event_id = event.id

        with pytest.raises(SQLAlchemyError):
            session.execute(
                update(AuditEvent)
                .where(AuditEvent.id == event_id)
                .values(action=AuditAction.TICKET_CREATED.value)
            )
            session.commit()
        session.rollback()

        with pytest.raises(SQLAlchemyError):
            session.delete(session.get(AuditEvent, event_id))
            session.commit()
        session.rollback()

        unchanged = session.get(AuditEvent, event_id)
        assert unchanged is not None
        assert unchanged.action == AuditAction.ORGANIZATION_MEMBER_CREATED


def test_audit_rows_reject_truncate_and_existing_rows_remain(
    audit_client: TestClient,
    database_engine: Engine,
) -> None:
    admin = register(audit_client, f"truncate-admin-{uuid4().hex}")
    target = register(audit_client, f"truncate-target-{uuid4().hex}")
    token = login(audit_client, admin["user"]["email"])
    organization_id = UUID(admin["organization"]["id"])
    assert (
        add_agent(
            audit_client,
            token=token,
            organization_id=str(organization_id),
            email=target["user"]["email"],
        ).status_code
        == 201
    )

    with Session(database_engine) as session:
        existing_event_id = session.scalar(
            select(AuditEvent.id).where(AuditEvent.organization_id == organization_id)
        )
        assert existing_event_id is not None

        with pytest.raises(SQLAlchemyError):
            session.execute(text("TRUNCATE TABLE audit_events"))
        session.rollback()

    with Session(database_engine) as verification_session:
        preserved_event = verification_session.get(AuditEvent, existing_event_id)
    assert preserved_event is not None
    assert preserved_event.action == AuditAction.ORGANIZATION_MEMBER_CREATED


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
def test_audit_endpoint_exposes_no_mutation_operations(
    audit_client: TestClient,
    method: str,
) -> None:
    response = audit_client.request(method, "/api/v1/audit-events")
    assert response.status_code == 405
