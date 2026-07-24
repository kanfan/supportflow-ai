from collections.abc import Iterator
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.identity.models import (
    MembershipRole,
    Organization,
    OrganizationMember,
    User,
)
from app.main import create_app
from app.tickets.models import (
    Customer,
    MessageAuthorType,
    Ticket,
    TicketMessage,
    TicketSourceType,
    TicketStatus,
)
from app.tickets.service import (
    InvalidTicketTransitionError,
    TicketPersistenceError,
    TicketService,
)


pytestmark = pytest.mark.integration
TEST_AUTH_SECRET = "ticket-integration-secret-with-thirty-two-bytes"


@pytest.fixture(scope="module")
def ticket_client(migrated_database_url: str) -> Iterator[TestClient]:
    application = create_app(
        Settings(
            environment="test",
            database_url=migrated_database_url,
            auth_secret_key=SecretStr(TEST_AUTH_SECRET),
            auth_issuer="supportflow-ticket-integration-test",
            auth_audience="supportflow-api-ticket-integration-test",
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


def register_and_login(client: TestClient, prefix: str) -> tuple[dict[str, Any], str]:
    registration_response = client.post(
        "/api/v1/auth/register",
        json=registration_payload(prefix),
    )
    assert registration_response.status_code == 201, registration_response.text
    registration = registration_response.json()

    login_response = client.post(
        "/api/v1/auth/login",
        json={
            "email": f"{prefix}@example.com",
            "password": "correct horse battery staple",
        },
    )
    assert login_response.status_code == 200, login_response.text
    return registration, str(login_response.json()["access_token"])


def tenant_headers(token: str, organization_id: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Organization-ID": organization_id,
    }


def create_ticket_payload(subject: str) -> dict[str, object]:
    return {
        "subject": subject,
        "initial_message": {
            "author_type": "agent",
            "body": "Customer reported a reproducible problem.",
        },
    }


def test_register_login_create_and_list_ticket_flow(
    ticket_client: TestClient,
) -> None:
    prefix = f"ticket-flow-{uuid4().hex}"
    registration, token = register_and_login(ticket_client, prefix)
    organization_id = str(registration["organization"]["id"])
    user_id = registration["user"]["id"]
    headers = tenant_headers(token, organization_id)

    create_response = ticket_client.post(
        "/api/v1/tickets",
        headers=headers,
        json=create_ticket_payload("Unable to export a report"),
    )

    assert create_response.status_code == 201, create_response.text
    created = create_response.json()
    assert created["organization_id"] == organization_id
    assert created["status"] == "open"
    assert created["initial_message"]["ticket_id"] == created["id"]
    assert created["initial_message"]["author_type"] == "agent"
    assert created["initial_message"]["author_user_id"] == user_id
    assert created["initial_message"]["author_customer_id"] is None

    list_response = ticket_client.get(
        "/api/v1/tickets",
        headers=headers,
    )
    assert list_response.status_code == 200
    body = list_response.json()
    assert body["pagination"] == {"limit": 20, "offset": 0, "total": 1}
    assert [ticket["id"] for ticket in body["items"]] == [created["id"]]


def test_ticket_listing_and_customer_lookup_are_tenant_scoped(
    ticket_client: TestClient,
    database_engine: Engine,
) -> None:
    first_prefix = f"tenant-a-{uuid4().hex}"
    second_prefix = f"tenant-b-{uuid4().hex}"
    first_registration, first_token = register_and_login(ticket_client, first_prefix)
    second_registration, second_token = register_and_login(
        ticket_client,
        second_prefix,
    )
    first_organization_id = str(first_registration["organization"]["id"])
    second_organization_id = str(second_registration["organization"]["id"])

    first_create = ticket_client.post(
        "/api/v1/tickets",
        headers=tenant_headers(first_token, first_organization_id),
        json=create_ticket_payload("Tenant A ticket"),
    )
    second_create = ticket_client.post(
        "/api/v1/tickets",
        headers=tenant_headers(second_token, second_organization_id),
        json=create_ticket_payload("Tenant B ticket"),
    )
    assert first_create.status_code == 201
    assert second_create.status_code == 201

    first_tenant_list = ticket_client.get(
        "/api/v1/tickets",
        headers=tenant_headers(first_token, first_organization_id),
    )
    assert first_tenant_list.status_code == 200
    first_tenant_ticket_ids = {
        ticket["id"] for ticket in first_tenant_list.json()["items"]
    }
    assert first_create.json()["id"] in first_tenant_ticket_ids
    assert second_create.json()["id"] not in first_tenant_ticket_ids

    cross_tenant_list = ticket_client.get(
        "/api/v1/tickets",
        headers=tenant_headers(first_token, second_organization_id),
    )
    assert cross_tenant_list.status_code == 404

    with Session(database_engine) as session:
        second_customer = Customer(
            organization_id=second_registration["organization"]["id"],
            name="Tenant B Customer",
            email="customer-b@example.com",
        )
        session.add(second_customer)
        session.commit()
        second_customer_id = str(second_customer.id)

    cross_tenant_customer = ticket_client.post(
        "/api/v1/tickets",
        headers=tenant_headers(first_token, first_organization_id),
        json={
            "subject": "Forged customer relationship",
            "customer_id": second_customer_id,
            "initial_message": {
                "author_type": "customer",
                "body": "This customer belongs to another tenant.",
            },
        },
    )
    assert cross_tenant_customer.status_code == 404
    assert cross_tenant_customer.json()["error"]["code"] == "not_found"


def test_database_rejects_cross_tenant_and_invalid_message_authors(
    database_session: Session,
) -> None:
    first_organization = Organization(name="First", slug=f"first-{uuid4().hex}")
    second_organization = Organization(name="Second", slug=f"second-{uuid4().hex}")
    first_user = User(
        email=f"first-{uuid4().hex}@example.com",
        password_hash="not-a-real-password-hash",
    )
    second_user = User(
        email=f"second-{uuid4().hex}@example.com",
        password_hash="not-a-real-password-hash",
    )
    first_membership = OrganizationMember(
        organization=first_organization,
        user=first_user,
        role=MembershipRole.ADMIN,
    )
    second_membership = OrganizationMember(
        organization=second_organization,
        user=second_user,
        role=MembershipRole.AGENT,
    )
    database_session.add_all([first_membership, second_membership])
    database_session.flush()

    first_customer = Customer(
        organization_id=first_organization.id,
        name="First Customer",
    )
    other_first_customer = Customer(
        organization_id=first_organization.id,
        name="Other First Customer",
    )
    second_customer = Customer(
        organization_id=second_organization.id,
        name="Second Customer",
    )
    database_session.add_all([first_customer, other_first_customer, second_customer])
    database_session.flush()

    invalid_ticket = Ticket(
        organization_id=first_organization.id,
        customer_id=second_customer.id,
        subject="Cross-tenant customer",
    )
    with pytest.raises(IntegrityError), database_session.begin_nested():
        database_session.add(invalid_ticket)
        database_session.flush()

    valid_ticket = Ticket(
        organization_id=first_organization.id,
        customer_id=first_customer.id,
        subject="Valid tenant relationship",
    )
    database_session.add(valid_ticket)
    database_session.flush()

    valid_customer_message = TicketMessage(
        organization_id=first_organization.id,
        ticket_id=valid_ticket.id,
        author_type=MessageAuthorType.CUSTOMER,
        author_customer_id=first_customer.id,
        body="Valid matching customer",
    )
    valid_system_message = TicketMessage(
        organization_id=first_organization.id,
        ticket_id=valid_ticket.id,
        author_type=MessageAuthorType.SYSTEM,
        body="Valid system event",
    )
    database_session.add_all([valid_customer_message, valid_system_message])
    database_session.flush()
    assert valid_customer_message.id is not None
    assert valid_system_message.id is not None

    invalid_messages = [
        TicketMessage(
            organization_id=first_organization.id,
            ticket_id=valid_ticket.id,
            author_type=MessageAuthorType.CUSTOMER,
            body="Customer identity is missing",
        ),
        TicketMessage(
            organization_id=first_organization.id,
            ticket_id=valid_ticket.id,
            author_type=MessageAuthorType.CUSTOMER,
            author_user_id=first_user.id,
            author_customer_id=first_customer.id,
            body="Customer cannot also be an agent",
        ),
        TicketMessage(
            organization_id=first_organization.id,
            ticket_id=valid_ticket.id,
            author_type=MessageAuthorType.AGENT,
            author_user_id=first_user.id,
            author_customer_id=first_customer.id,
            body="Agent cannot also be a customer",
        ),
        TicketMessage(
            organization_id=first_organization.id,
            ticket_id=valid_ticket.id,
            author_type=MessageAuthorType.SYSTEM,
            author_user_id=first_user.id,
            body="System cannot use a user identity",
        ),
        TicketMessage(
            organization_id=first_organization.id,
            ticket_id=valid_ticket.id,
            author_type=MessageAuthorType.SYSTEM,
            author_customer_id=first_customer.id,
            body="System cannot use a customer identity",
        ),
        TicketMessage(
            organization_id=first_organization.id,
            ticket_id=valid_ticket.id,
            author_type=MessageAuthorType.CUSTOMER,
            author_customer_id=second_customer.id,
            body="Wrong tenant customer",
        ),
        TicketMessage(
            organization_id=first_organization.id,
            ticket_id=valid_ticket.id,
            author_type=MessageAuthorType.CUSTOMER,
            author_customer_id=other_first_customer.id,
            body="Wrong customer for this ticket",
        ),
        TicketMessage(
            organization_id=first_organization.id,
            ticket_id=valid_ticket.id,
            author_type=MessageAuthorType.AGENT,
            author_user_id=second_user.id,
            body="Wrong tenant agent",
        ),
        TicketMessage(
            organization_id=first_organization.id,
            ticket_id=valid_ticket.id,
            author_type=MessageAuthorType.AGENT,
            body="Missing agent identity",
        ),
    ]
    for invalid_message in invalid_messages:
        with pytest.raises(IntegrityError), database_session.begin_nested():
            database_session.add(invalid_message)
            database_session.flush()


def test_failed_initial_message_rolls_back_ticket(
    database_engine: Engine,
) -> None:
    with Session(database_engine) as session:
        organization = Organization(name="Rollback", slug=f"rollback-{uuid4().hex}")
        user = User(
            email=f"rollback-{uuid4().hex}@example.com",
            password_hash="not-a-real-password-hash",
        )
        membership = OrganizationMember(
            organization=organization,
            user=user,
            role=MembershipRole.ADMIN,
        )
        session.add(membership)
        session.commit()

        service = TicketService(session, organization.id)
        with pytest.raises(TicketPersistenceError):
            service.create_ticket(
                subject="Must roll back",
                source_type=TicketSourceType.MANUAL,
                external_id=None,
                customer_id=None,
                initial_message_body="",
                initial_message_author_type=MessageAuthorType.AGENT,
                current_user_id=user.id,
            )

        ticket_count = session.scalar(
            select(func.count())
            .select_from(Ticket)
            .where(Ticket.organization_id == organization.id)
        )
        assert ticket_count == 0


def test_ticket_status_transitions_are_strict(
    database_session: Session,
) -> None:
    organization = Organization(name="Transitions", slug=f"status-{uuid4().hex}")
    user = User(
        email=f"status-{uuid4().hex}@example.com",
        password_hash="not-a-real-password-hash",
    )
    membership = OrganizationMember(
        organization=organization,
        user=user,
        role=MembershipRole.ADMIN,
    )
    database_session.add(membership)
    database_session.commit()

    service = TicketService(database_session, organization.id)
    created = service.create_ticket(
        subject="Transition test",
        source_type=TicketSourceType.API,
        external_id=None,
        customer_id=None,
        initial_message_body="Start the workflow",
        initial_message_author_type=MessageAuthorType.AGENT,
        current_user_id=user.id,
    )

    with pytest.raises(InvalidTicketTransitionError):
        service.transition_ticket(
            ticket_id=created.ticket.id,
            next_status=TicketStatus.RESOLVED,
        )

    for expected_status in (
        TicketStatus.PROCESSING,
        TicketStatus.WAITING_FOR_AGENT,
        TicketStatus.RESOLVED,
        TicketStatus.CLOSED,
    ):
        transitioned = service.transition_ticket(
            ticket_id=created.ticket.id,
            next_status=expected_status,
        )
        assert transitioned.status is expected_status

    with pytest.raises(InvalidTicketTransitionError):
        service.transition_ticket(
            ticket_id=created.ticket.id,
            next_status=TicketStatus.OPEN,
        )
