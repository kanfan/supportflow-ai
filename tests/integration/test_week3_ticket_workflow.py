from collections.abc import Iterator
import json
import re
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.audit.models import AuditAction, AuditEvent
from app.config import Settings
from app.main import create_app
from app.tickets.models import Customer, Ticket, TicketMessage, TicketStatus
from app.ui.session import UI_SESSION_COOKIE


pytestmark = pytest.mark.integration
TEST_AUTH_SECRET = "week3-ticket-secret-with-at-least-thirty-two-bytes"
PASSWORD = "correct horse battery staple"


@pytest.fixture(scope="module")
def workflow_client(migrated_database_url: str) -> Iterator[TestClient]:
    application = create_app(
        Settings(
            environment="test",
            database_url=migrated_database_url,
            auth_secret_key=SecretStr(TEST_AUTH_SECRET),
            auth_issuer="supportflow-week3-ticket-test",
            auth_audience="supportflow-week3-ticket-api",
        )
    )
    with TestClient(application) as client:
        yield client


def register(client: TestClient, prefix: str) -> dict[str, Any]:
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"{prefix}@example.com",
            "password": PASSWORD,
            "organization_name": f"{prefix} Organization",
            "organization_slug": prefix,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def api_login(client: TestClient, email: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


def tenant_headers(token: str, organization_id: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Organization-ID": organization_id,
    }


def create_ticket(
    client: TestClient,
    *,
    headers: dict[str, str],
    subject: str,
    source_type: str = "manual",
    customer_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "subject": subject,
        "source_type": source_type,
        "initial_message": {
            "author_type": "agent",
            "body": f"Initial message for {subject}",
        },
    }
    if customer_id is not None:
        payload["customer_id"] = customer_id
    response = client.post("/api/v1/tickets", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def csrf_from_html(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


def test_ticket_api_detail_filters_messages_transitions_and_audit(
    workflow_client: TestClient,
    database_engine: Engine,
) -> None:
    first = register(workflow_client, f"api-a-{uuid4().hex}")
    second = register(workflow_client, f"api-b-{uuid4().hex}")
    first_token = api_login(workflow_client, first["user"]["email"])
    second_token = api_login(workflow_client, second["user"]["email"])
    first_headers = tenant_headers(first_token, first["organization"]["id"])
    second_headers = tenant_headers(second_token, second["organization"]["id"])

    with Session(database_engine) as session:
        customer = Customer(
            organization_id=first["organization"]["id"],
            name="Filtered Customer",
            email=f"customer-{uuid4().hex}@example.com",
        )
        session.add(customer)
        session.commit()
        customer_id = str(customer.id)

    first_ticket = create_ticket(
        workflow_client,
        headers=first_headers,
        subject="Tenant A manual ticket",
        customer_id=customer_id,
    )
    create_ticket(
        workflow_client,
        headers=first_headers,
        subject="Tenant A API ticket",
        source_type="api",
    )
    second_ticket = create_ticket(
        workflow_client,
        headers=second_headers,
        subject="Tenant B secret ticket",
    )

    filtered = workflow_client.get(
        "/api/v1/tickets",
        headers=first_headers,
        params={
            "status": "open",
            "source_type": "manual",
            "customer_id": customer_id,
        },
    )
    assert filtered.status_code == 200
    assert filtered.json()["pagination"]["total"] == 1
    assert [item["id"] for item in filtered.json()["items"]] == [first_ticket["id"]]

    detail = workflow_client.get(
        f"/api/v1/tickets/{first_ticket['id']}",
        headers=first_headers,
    )
    assert detail.status_code == 200
    assert detail.json()["messages"][0]["id"] == first_ticket["initial_message"]["id"]

    impersonation_attempts = [
        {"body": "forged", "author_user_id": str(uuid4())},
        {"body": "forged", "author_type": "customer"},
        {"body": "forged", "author_customer_id": customer_id},
    ]
    for payload in impersonation_attempts:
        response = workflow_client.post(
            f"/api/v1/tickets/{first_ticket['id']}/messages",
            headers=first_headers,
            json=payload,
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"

    message_response = workflow_client.post(
        f"/api/v1/tickets/{first_ticket['id']}/messages",
        headers=first_headers,
        json={"body": "Agent-authored follow-up without impersonation fields."},
    )
    assert message_response.status_code == 201
    assert message_response.json()["author_type"] == "agent"
    assert message_response.json()["author_user_id"] == first["user"]["id"]

    with Session(database_engine) as session:
        audit_count_before_invalid = session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.organization_id == first["organization"]["id"])
        )

    invalid_transition = workflow_client.patch(
        f"/api/v1/tickets/{first_ticket['id']}/status",
        headers=first_headers,
        json={"status": "resolved"},
    )
    assert invalid_transition.status_code == 409
    assert invalid_transition.json()["error"] == {
        "code": "invalid_ticket_transition",
        "message": "The requested ticket status transition is not allowed",
        "details": {
            "current_status": "open",
            "requested_status": "resolved",
        },
    }

    valid_transition = workflow_client.patch(
        f"/api/v1/tickets/{first_ticket['id']}/status",
        headers=first_headers,
        json={"status": "processing"},
    )
    assert valid_transition.status_code == 200
    assert valid_transition.json()["status"] == "processing"

    for path, method, payload in (
        (f"/api/v1/tickets/{second_ticket['id']}", "get", None),
        (
            f"/api/v1/tickets/{second_ticket['id']}/messages",
            "post",
            {"body": "Cross-tenant write"},
        ),
        (
            f"/api/v1/tickets/{second_ticket['id']}/status",
            "patch",
            {"status": "processing"},
        ),
    ):
        response = workflow_client.request(
            method,
            path,
            headers=first_headers,
            json=payload,
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    with Session(database_engine) as session:
        first_ticket_row = session.get(Ticket, first_ticket["id"])
        assert first_ticket_row is not None
        assert first_ticket_row.status.value == "processing"
        audit_events = list(
            session.scalars(
                select(AuditEvent)
                .where(AuditEvent.organization_id == first["organization"]["id"])
                .order_by(AuditEvent.created_at, AuditEvent.id)
            )
        )
        audit_count_after = len(audit_events)

    assert audit_count_before_invalid is not None
    assert audit_count_after == audit_count_before_invalid + 1
    assert {event.action for event in audit_events} >= {
        AuditAction.TICKET_CREATED,
        AuditAction.TICKET_MESSAGE_CREATED,
        AuditAction.TICKET_STATUS_CHANGED,
    }
    serialized_audit = json.dumps(
        [event.event_metadata for event in audit_events]
    ).lower()
    assert "initial message for" not in serialized_audit
    assert "agent-authored follow-up" not in serialized_audit


def test_audit_failure_rolls_back_agent_message_and_status(
    workflow_client: TestClient,
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registration = register(workflow_client, f"rollback-{uuid4().hex}")
    token = api_login(workflow_client, registration["user"]["email"])
    headers = tenant_headers(token, registration["organization"]["id"])
    ticket = create_ticket(
        workflow_client,
        headers=headers,
        subject="Message rollback",
    )
    organization_id = UUID(registration["organization"]["id"])
    ticket_id = UUID(ticket["id"])

    with Session(database_engine) as session:
        initial_message_count = session.scalar(
            select(func.count())
            .select_from(TicketMessage)
            .where(TicketMessage.ticket_id == ticket_id)
        )
        initial_message_audit_count = session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.organization_id == organization_id,
                AuditEvent.action == AuditAction.TICKET_MESSAGE_CREATED,
            )
        )
        initial_status_audit_count = session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.organization_id == organization_id,
                AuditEvent.action == AuditAction.TICKET_STATUS_CHANGED,
            )
        )
    assert initial_message_count is not None
    assert initial_message_audit_count is not None
    assert initial_status_audit_count is not None

    expected_operation = "message"
    flushed_operations: list[str] = []

    def flush_then_fail(session: Session) -> None:
        session.flush()
        if expected_operation == "message":
            staged_message_count = session.scalar(
                select(func.count())
                .select_from(TicketMessage)
                .where(
                    TicketMessage.ticket_id == ticket_id,
                    TicketMessage.body == "This message must roll back.",
                )
            )
            staged_audit_count = session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.organization_id == organization_id,
                    AuditEvent.action == AuditAction.TICKET_MESSAGE_CREATED,
                )
            )
            assert staged_message_count == 1
            assert staged_audit_count == initial_message_audit_count + 1
        else:
            staged_status = session.scalar(
                select(Ticket.status).where(Ticket.id == ticket_id)
            )
            staged_audit_count = session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.organization_id == organization_id,
                    AuditEvent.action == AuditAction.TICKET_STATUS_CHANGED,
                )
            )
            assert staged_status is TicketStatus.PROCESSING
            assert staged_audit_count == initial_status_audit_count + 1
        flushed_operations.append(expected_operation)
        raise RuntimeError("forced ticket audit failure")

    monkeypatch.setattr(Session, "commit", flush_then_fail)
    with pytest.raises(RuntimeError, match="forced ticket audit failure"):
        workflow_client.post(
            f"/api/v1/tickets/{ticket['id']}/messages",
            headers=headers,
            json={"body": "This message must roll back."},
        )

    with Session(database_engine) as session:
        message_count = session.scalar(
            select(func.count())
            .select_from(TicketMessage)
            .where(TicketMessage.ticket_id == ticket_id)
        )
        ticket_status = session.scalar(
            select(Ticket.status).where(Ticket.id == ticket_id)
        )
        message_audit_count = session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.organization_id == organization_id,
                AuditEvent.action == AuditAction.TICKET_MESSAGE_CREATED,
            )
        )
    assert flushed_operations == ["message"]
    assert message_count == initial_message_count
    assert message_audit_count == initial_message_audit_count
    assert ticket_status is not None
    assert ticket_status.value == "open"

    expected_operation = "status"
    with pytest.raises(RuntimeError, match="forced ticket audit failure"):
        workflow_client.patch(
            f"/api/v1/tickets/{ticket['id']}/status",
            headers=headers,
            json={"status": "processing"},
        )

    with Session(database_engine) as session:
        rolled_back_status = session.scalar(
            select(Ticket.status).where(Ticket.id == ticket_id)
        )
        status_audit_count = session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.organization_id == organization_id,
                AuditEvent.action == AuditAction.TICKET_STATUS_CHANGED,
            )
        )
    assert flushed_operations == ["message", "status"]
    assert rolled_back_status is not None
    assert rolled_back_status.value == "open"
    assert status_audit_count == initial_status_audit_count


def test_agent_ui_session_csrf_tenant_workflow_and_logout_invalidation(
    workflow_client: TestClient,
    database_engine: Engine,
) -> None:
    admin = register(workflow_client, f"ui-admin-{uuid4().hex}")
    agent = register(workflow_client, f"ui-agent-{uuid4().hex}")
    other = register(workflow_client, f"ui-other-{uuid4().hex}")
    admin_token = api_login(workflow_client, admin["user"]["email"])
    other_token = api_login(workflow_client, other["user"]["email"])
    admin_headers = tenant_headers(admin_token, admin["organization"]["id"])
    other_headers = tenant_headers(other_token, other["organization"]["id"])

    membership_response = workflow_client.post(
        "/api/v1/organization-members",
        headers=admin_headers,
        json={"email": agent["user"]["email"], "role": "agent"},
    )
    assert membership_response.status_code == 201

    visible_ticket = create_ticket(
        workflow_client,
        headers=admin_headers,
        subject="Visible tenant ticket",
    )
    create_ticket(
        workflow_client,
        headers=other_headers,
        subject="Other tenant secret ticket",
    )

    login_page = workflow_client.get("/ui/login")
    assert login_page.status_code == 200
    anonymous_cookie = workflow_client.cookies.get(UI_SESSION_COOKIE)
    assert anonymous_cookie is not None
    anonymous_csrf = csrf_from_html(login_page.text)

    missing_csrf_login = workflow_client.post(
        "/ui/login",
        data={
            "email": agent["user"]["email"],
            "password": PASSWORD,
            "organization_slug": admin["organization"]["slug"],
        },
        follow_redirects=False,
    )
    assert missing_csrf_login.status_code == 403

    login_response = workflow_client.post(
        "/ui/login",
        data={
            "csrf_token": anonymous_csrf,
            "email": agent["user"]["email"],
            "password": PASSWORD,
            "organization_slug": admin["organization"]["slug"],
        },
        follow_redirects=False,
    )
    assert login_response.status_code == 303
    authenticated_cookie = workflow_client.cookies.get(UI_SESSION_COOKIE)
    assert authenticated_cookie is not None
    assert authenticated_cookie != anonymous_cookie
    set_cookie = login_response.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "bearer" not in set_cookie
    assert "access_token" not in set_cookie

    workflow_client.cookies.set(UI_SESSION_COOKIE, anonymous_cookie)
    replay_old_session = workflow_client.get("/ui/tickets", follow_redirects=False)
    assert replay_old_session.status_code == 303
    assert replay_old_session.headers["location"] == "/ui/login"
    workflow_client.cookies.set(UI_SESSION_COOKIE, authenticated_cookie)

    ticket_list = workflow_client.get("/ui/tickets")
    assert ticket_list.status_code == 200
    assert "Visible tenant ticket" in ticket_list.text
    assert "Other tenant secret ticket" not in ticket_list.text

    detail = workflow_client.get(f"/ui/tickets/{visible_ticket['id']}")
    assert detail.status_code == 200
    authenticated_csrf = csrf_from_html(detail.text)
    assert authenticated_csrf != anonymous_csrf

    missing_csrf_message = workflow_client.post(
        f"/ui/tickets/{visible_ticket['id']}/messages",
        data={"body": "Blocked by CSRF"},
        follow_redirects=False,
    )
    assert missing_csrf_message.status_code == 403

    message_response = workflow_client.post(
        f"/ui/tickets/{visible_ticket['id']}/messages",
        data={
            "csrf_token": authenticated_csrf,
            "body": "Agent UI follow-up",
        },
        follow_redirects=False,
    )
    assert message_response.status_code == 303

    transition_response = workflow_client.post(
        f"/ui/tickets/{visible_ticket['id']}/status",
        data={"csrf_token": authenticated_csrf, "status": "processing"},
        follow_redirects=False,
    )
    assert transition_response.status_code == 303

    with Session(database_engine) as session:
        event_actions = set(
            session.scalars(
                select(AuditEvent.action).where(
                    AuditEvent.organization_id == admin["organization"]["id"]
                )
            )
        )
    assert AuditAction.TICKET_MESSAGE_CREATED in event_actions
    assert AuditAction.TICKET_STATUS_CHANGED in event_actions

    logout_response = workflow_client.post(
        "/ui/logout",
        data={"csrf_token": authenticated_csrf},
        follow_redirects=False,
    )
    assert logout_response.status_code == 303

    workflow_client.cookies.set(UI_SESSION_COOKIE, authenticated_cookie)
    replay_after_logout = workflow_client.get("/ui/tickets", follow_redirects=False)
    assert replay_after_logout.status_code == 303
    assert replay_after_logout.headers["location"] == "/ui/login"


def test_production_like_ui_cookie_is_secure(migrated_database_url: str) -> None:
    application = create_app(
        Settings(
            environment="staging",
            database_url=migrated_database_url,
            auth_secret_key=SecretStr(TEST_AUTH_SECRET),
            auth_issuer="supportflow-week3-staging-test",
            auth_audience="supportflow-week3-staging-api",
        )
    )
    with TestClient(application, base_url="https://testserver") as client:
        response = client.get("/ui/login")
    assert response.status_code == 200
    assert "secure" in response.headers["set-cookie"].lower()
