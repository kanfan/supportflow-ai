from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest
from pydantic import SecretStr
from sqlalchemy import select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.config import Settings
from app.identity.models import (
    MembershipRole,
    MembershipStatus,
    OrganizationMember,
    User,
    UserStatus,
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

    admin_members_body = admin_members.json()
    agent_own_members_body = agent_own_members.json()
    assert admin_members.status_code == 200
    assert admin_members_body["pagination"] == {
        "limit": 20,
        "offset": 0,
        "total": 2,
    }
    assert {member["user_id"] for member in admin_members_body["items"]} == {
        admin["user"]["id"],
        agent["user"]["id"],
    }
    assert agent_own_members.status_code == 200
    assert agent_own_members_body["pagination"] == {
        "limit": 20,
        "offset": 0,
        "total": 1,
    }
    assert {member["user_id"] for member in agent_own_members_body["items"]} == {
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


def test_membership_list_paginates_in_deterministic_descending_order(
    membership_client: TestClient,
    database_engine: Engine,
) -> None:
    admin_prefix = f"page-admin-{uuid4().hex}"
    admin = register(membership_client, admin_prefix)
    targets = [
        register(membership_client, f"page-target-{uuid4().hex}") for _ in range(4)
    ]
    admin_token = login(membership_client, admin["user"]["email"])
    organization_id = UUID(admin["organization"]["id"])
    target_ids = [UUID(target["user"]["id"]) for target in targets]
    created_times = [
        datetime(2030, 1, 1, tzinfo=UTC),
        datetime(2029, 1, 1, tzinfo=UTC),
        datetime(2029, 1, 1, tzinfo=UTC),
        datetime(2025, 1, 1, tzinfo=UTC),
    ]

    with Session(database_engine) as session:
        session.add_all(
            [
                OrganizationMember(
                    organization_id=organization_id,
                    user_id=user_id,
                    role=MembershipRole.AGENT,
                    created_at=created_at,
                )
                for user_id, created_at in zip(
                    target_ids,
                    created_times,
                    strict=True,
                )
            ]
        )
        session.commit()

    tie_breaker_ids = sorted(target_ids[1:3], reverse=True)
    first_page = membership_client.get(
        "/api/v1/organization-members",
        params={"limit": 3, "offset": 0},
        headers=bearer_headers(admin_token, str(organization_id)),
    )
    second_page = membership_client.get(
        "/api/v1/organization-members",
        params={"limit": 2, "offset": 3},
        headers=bearer_headers(admin_token, str(organization_id)),
    )

    assert first_page.status_code == 200
    assert [UUID(item["user_id"]) for item in first_page.json()["items"]] == [
        target_ids[0],
        *tie_breaker_ids,
    ]
    assert first_page.json()["pagination"] == {
        "limit": 3,
        "offset": 0,
        "total": 5,
    }
    assert second_page.status_code == 200
    assert [UUID(item["user_id"]) for item in second_page.json()["items"]] == [
        UUID(admin["user"]["id"]),
        target_ids[3],
    ]
    assert second_page.json()["pagination"] == {
        "limit": 2,
        "offset": 3,
        "total": 5,
    }


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
    ],
)
def test_membership_list_rejects_invalid_pagination(
    membership_client: TestClient,
    params: dict[str, int],
) -> None:
    admin_prefix = f"page-bounds-{uuid4().hex}"
    admin = register(membership_client, admin_prefix)
    admin_token = login(membership_client, admin["user"]["email"])

    response = membership_client.get(
        "/api/v1/organization-members",
        params=params,
        headers=bearer_headers(admin_token, admin["organization"]["id"]),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


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


def test_create_rejects_impersonation_shaped_extra_fields(
    membership_client: TestClient,
    database_engine: Engine,
) -> None:
    admin_prefix = f"extra-admin-{uuid4().hex}"
    target_prefix = f"extra-target-{uuid4().hex}"
    admin = register(membership_client, admin_prefix)
    target = register(membership_client, target_prefix)
    admin_token = login(membership_client, admin["user"]["email"])
    base_payload = {"email": target["user"]["email"], "role": "agent"}
    forged_fields: list[dict[str, str]] = [
        {"user_id": str(uuid4())},
        {"status": "active"},
        {"organization_id": str(uuid4())},
        {"actor_user_id": str(uuid4())},
    ]

    for forged_field in forged_fields:
        response = membership_client.post(
            "/api/v1/organization-members",
            headers=bearer_headers(admin_token, admin["organization"]["id"]),
            json={**base_payload, **forged_field},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"

    with Session(database_engine) as session:
        membership = session.get(
            OrganizationMember,
            (admin["organization"]["id"], target["user"]["id"]),
        )
    assert membership is None


def test_disabled_target_is_indistinguishable_from_missing_user(
    membership_client: TestClient,
    database_engine: Engine,
) -> None:
    admin_prefix = f"disabled-target-admin-{uuid4().hex}"
    target_prefix = f"disabled-target-{uuid4().hex}"
    admin = register(membership_client, admin_prefix)
    target = register(membership_client, target_prefix)
    admin_token = login(membership_client, admin["user"]["email"])

    with database_engine.begin() as connection:
        connection.execute(
            update(User)
            .where(User.id == target["user"]["id"])
            .values(status=UserStatus.DISABLED)
        )

    disabled_response = add_agent(
        membership_client,
        token=admin_token,
        organization_id=admin["organization"]["id"],
        email=target["user"]["email"],
    )
    missing_response = add_agent(
        membership_client,
        token=admin_token,
        organization_id=admin["organization"]["id"],
        email=f"missing-{uuid4().hex}@example.com",
    )

    assert disabled_response.status_code == 404
    assert disabled_response.json() == missing_response.json()
    assert disabled_response.json()["error"] == {
        "code": "not_found",
        "message": "User not found",
        "details": None,
    }

    with Session(database_engine) as session:
        membership = session.get(
            OrganizationMember,
            (admin["organization"]["id"], target["user"]["id"]),
        )
    assert membership is None


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
