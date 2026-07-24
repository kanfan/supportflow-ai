from collections.abc import Iterator
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.config import Settings
from app.identity.models import (
    MembershipRole,
    MembershipStatus,
    OrganizationMember,
)
from app.main import create_app


pytestmark = pytest.mark.integration
TEST_AUTH_SECRET = "membership-test-secret-with-thirty-two-bytes"


@pytest.fixture(scope="module")
def membership_client(migrated_database_url: str) -> Iterator[TestClient]:
    application = create_app(
        Settings(
            environment="test",
            database_url=migrated_database_url,
            auth_secret_key=SecretStr(TEST_AUTH_SECRET),
            auth_issuer="supportflow-membership-integration-test",
            auth_audience="supportflow-api-membership-test",
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
    role: str = "agent",
) -> Any:
    return client.post(
        "/api/v1/organization-members",
        headers=bearer_headers(token, organization_id),
        json={"email": email, "role": role},
    )


def test_admin_adds_existing_user_and_lists_only_selected_tenant(
    membership_client: TestClient,
    database_engine: Engine,
) -> None:
    admin_prefix = f"member-admin-{uuid4().hex}"
    agent_prefix = f"member-agent-{uuid4().hex}"
    admin = register(membership_client, admin_prefix)
    agent = register(membership_client, agent_prefix)
    admin_token = login(membership_client, admin["user"]["email"])
    agent_token = login(membership_client, agent["user"]["email"])

    created = add_agent(
        membership_client,
        token=admin_token,
        organization_id=admin["organization"]["id"],
        email=f"  {agent['user']['email'].upper()}  ",
    )

    assert created.status_code == 201
    assert created.json() == {
        "user_id": agent["user"]["id"],
        "email": agent["user"]["email"],
        "role": "agent",
        "status": "active",
        "created_at": created.json()["created_at"],
        "updated_at": created.json()["updated_at"],
    }

    admin_members = membership_client.get(
        "/api/v1/organization-members",
        headers=bearer_headers(admin_token, admin["organization"]["id"]),
    )
    agent_own_members = membership_client.get(
        "/api/v1/organization-members",
        headers=bearer_headers(agent_token, agent["organization"]["id"]),
    )
    cross_tenant_attempt = membership_client.get(
        "/api/v1/organization-members",
        headers=bearer_headers(admin_token, agent["organization"]["id"]),
    )

    assert admin_members.status_code == 200
    assert {member["user_id"] for member in admin_members.json()} == {
        admin["user"]["id"],
        agent["user"]["id"],
    }
    assert agent_own_members.status_code == 200
    assert {member["user_id"] for member in agent_own_members.json()} == {
        agent["user"]["id"]
    }
    assert cross_tenant_attempt.status_code == 404

    with Session(database_engine) as session:
        membership = session.get(
            OrganizationMember,
            (admin["organization"]["id"], agent["user"]["id"]),
        )
    assert membership is not None
    assert membership.role is MembershipRole.AGENT
    assert membership.status is MembershipStatus.ACTIVE


def test_agent_is_forbidden_from_membership_administration(
    membership_client: TestClient,
    database_engine: Engine,
) -> None:
    admin_prefix = f"forbidden-admin-{uuid4().hex}"
    agent_prefix = f"forbidden-agent-{uuid4().hex}"
    admin = register(membership_client, admin_prefix)
    agent = register(membership_client, agent_prefix)
    agent_token = login(membership_client, agent["user"]["email"])

    with Session(database_engine) as session:
        session.add(
            OrganizationMember(
                organization_id=admin["organization"]["id"],
                user_id=agent["user"]["id"],
                role=MembershipRole.AGENT,
            )
        )
        session.commit()

    headers = bearer_headers(agent_token, admin["organization"]["id"])
    list_response = membership_client.get(
        "/api/v1/organization-members",
        headers=headers,
    )
    create_response = membership_client.post(
        "/api/v1/organization-members",
        headers=headers,
        json={"email": admin["user"]["email"], "role": "agent"},
    )

    for response in (list_response, create_response):
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "forbidden"


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("authorization", [None, "Bearer invalid-token"])
def test_membership_endpoints_require_valid_authentication(
    membership_client: TestClient,
    method: str,
    authorization: str | None,
) -> None:
    headers = {"X-Organization-ID": str(uuid4())}
    if authorization is not None:
        headers["Authorization"] = authorization
    request_kwargs: dict[str, Any] = {"headers": headers}
    if method == "POST":
        request_kwargs["json"] = {
            "email": "existing@example.com",
            "role": "agent",
        }

    response = membership_client.request(
        method,
        "/api/v1/organization-members",
        **request_kwargs,
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "unauthorized"


def test_duplicate_membership_returns_conflict_without_changing_role(
    membership_client: TestClient,
    database_engine: Engine,
) -> None:
    admin_prefix = f"duplicate-admin-{uuid4().hex}"
    agent_prefix = f"duplicate-agent-{uuid4().hex}"
    admin = register(membership_client, admin_prefix)
    agent = register(membership_client, agent_prefix)
    admin_token = login(membership_client, admin["user"]["email"])

    first_response = add_agent(
        membership_client,
        token=admin_token,
        organization_id=admin["organization"]["id"],
        email=agent["user"]["email"],
    )
    duplicate_response = add_agent(
        membership_client,
        token=admin_token,
        organization_id=admin["organization"]["id"],
        email=agent["user"]["email"],
    )

    assert first_response.status_code == 201
    assert duplicate_response.status_code == 409
    assert duplicate_response.json()["error"]["code"] == "conflict"

    with Session(database_engine) as session:
        memberships = list(
            session.scalars(
                select(OrganizationMember).where(
                    OrganizationMember.organization_id == admin["organization"]["id"],
                    OrganizationMember.user_id == agent["user"]["id"],
                )
            )
        )
    assert len(memberships) == 1
    assert memberships[0].role is MembershipRole.AGENT


def test_create_rejects_admin_role_and_unknown_user(
    membership_client: TestClient,
    database_engine: Engine,
) -> None:
    admin_prefix = f"validation-admin-{uuid4().hex}"
    target_prefix = f"validation-target-{uuid4().hex}"
    admin = register(membership_client, admin_prefix)
    target = register(membership_client, target_prefix)
    admin_token = login(membership_client, admin["user"]["email"])

    admin_role_response = add_agent(
        membership_client,
        token=admin_token,
        organization_id=admin["organization"]["id"],
        email=target["user"]["email"],
        role="admin",
    )
    unknown_user_response = add_agent(
        membership_client,
        token=admin_token,
        organization_id=admin["organization"]["id"],
        email=f"missing-{uuid4().hex}@example.com",
    )

    assert admin_role_response.status_code == 422
    assert admin_role_response.json()["error"]["code"] == "validation_error"
    assert unknown_user_response.status_code == 404
    assert unknown_user_response.json()["error"]["code"] == "not_found"

    with Session(database_engine) as session:
        membership = session.get(
            OrganizationMember,
            (admin["organization"]["id"], target["user"]["id"]),
        )
    assert membership is None
