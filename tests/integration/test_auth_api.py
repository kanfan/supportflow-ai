from collections.abc import Iterator
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
import pytest
from pydantic import SecretStr
from sqlalchemy import select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.api.dependencies import (
    OrganizationContext,
    get_organization_context,
    require_roles,
)
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


pytestmark = pytest.mark.integration
TEST_AUTH_SECRET = "integration-test-secret-with-thirty-two-bytes"


@pytest.fixture(scope="module")
def auth_client(migrated_database_url: str) -> Iterator[TestClient]:
    application = create_app(
        Settings(
            environment="test",
            database_url=migrated_database_url,
            auth_secret_key=SecretStr(TEST_AUTH_SECRET),
            auth_issuer="supportflow-integration-test",
            auth_audience="supportflow-api-integration-test",
        )
    )
    test_router = APIRouter()

    @test_router.get("/__test__/organization-context")
    def read_organization_context(
        context: Annotated[
            OrganizationContext,
            Depends(get_organization_context),
        ],
    ) -> dict[str, str]:
        return {
            "organization_id": str(context.organization.id),
            "role": context.membership.role.value,
        }

    @test_router.get("/__test__/admin-context")
    def read_admin_context(
        context: Annotated[
            OrganizationContext,
            Depends(require_roles(MembershipRole.ADMIN)),
        ],
    ) -> dict[str, str]:
        return {"organization_id": str(context.organization.id)}

    application.include_router(test_router)
    with TestClient(application) as client:
        yield client


def registration_payload(prefix: str) -> dict[str, str]:
    return {
        "email": f"{prefix}@EXAMPLE.COM",
        "password": "correct horse battery staple",
        "organization_name": f"{prefix} Organization",
        "organization_slug": prefix.upper(),
    }


def register(auth_client: TestClient, prefix: str) -> dict[str, Any]:
    response = auth_client.post(
        "/api/v1/auth/register",
        json=registration_payload(prefix),
    )
    assert response.status_code == 201, response.text
    return response.json()


def login(auth_client: TestClient, email: str) -> dict[str, Any]:
    response = auth_client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": "correct horse battery staple",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def bearer_headers(token: str, organization_id: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if organization_id:
        headers["X-Organization-ID"] = organization_id
    return headers


def test_registration_is_atomic_normalized_and_hashes_password(
    auth_client: TestClient,
    database_engine: Engine,
) -> None:
    prefix = f"registration-{uuid4().hex}"

    body = register(auth_client, prefix)

    assert body["user"]["email"] == f"{prefix}@example.com"
    assert body["organization"]["slug"] == prefix
    assert body["role"] == "admin"
    assert body["membership_status"] == "active"
    assert "password" not in str(body).lower()

    with Session(database_engine) as session:
        user = session.get(User, body["user"]["id"])
        organization = session.get(Organization, body["organization"]["id"])
        membership = session.get(
            OrganizationMember,
            (body["organization"]["id"], body["user"]["id"]),
        )

    assert user is not None
    assert user.password_hash.startswith("$argon2id$")
    assert user.password_hash != "correct horse battery staple"
    assert organization is not None
    assert membership is not None


def test_registration_conflicts_use_the_standard_error_envelope(
    auth_client: TestClient,
    database_engine: Engine,
) -> None:
    prefix = f"conflict-{uuid4().hex}"
    register(auth_client, prefix)

    duplicate_email = registration_payload(f"other-{uuid4().hex}")
    duplicate_email["email"] = f"{prefix}@example.com"
    duplicate_email_response = auth_client.post(
        "/api/v1/auth/register",
        json=duplicate_email,
    )

    duplicate_slug = registration_payload(f"another-{uuid4().hex}")
    duplicate_slug["organization_slug"] = prefix
    duplicate_slug_response = auth_client.post(
        "/api/v1/auth/register",
        json=duplicate_slug,
    )

    for response in (duplicate_email_response, duplicate_slug_response):
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "conflict"

    with Session(database_engine) as session:
        rolled_back_organization = session.scalar(
            select(Organization).where(
                Organization.slug == duplicate_email["organization_slug"]
            )
        )
        rolled_back_user = session.scalar(
            select(User).where(User.email == duplicate_slug["email"])
        )

    assert rolled_back_organization is None
    assert rolled_back_user is None


def test_login_and_current_user_flow(auth_client: TestClient) -> None:
    prefix = f"login-{uuid4().hex}"
    registration = register(auth_client, prefix)

    token_response = login(auth_client, f"{prefix}@EXAMPLE.COM")

    assert token_response["token_type"] == "bearer"
    assert token_response["expires_in"] == 900
    me_response = auth_client.get(
        "/api/v1/auth/me",
        headers=bearer_headers(str(token_response["access_token"])),
    )
    assert me_response.status_code == 200
    assert me_response.json()["id"] == registration["user"]["id"]
    assert "password" not in str(me_response.json()).lower()


def test_login_failures_are_indistinguishable(auth_client: TestClient) -> None:
    prefix = f"bad-login-{uuid4().hex}"
    register(auth_client, prefix)

    wrong_password = auth_client.post(
        "/api/v1/auth/login",
        json={"email": f"{prefix}@example.com", "password": "wrong"},
    )
    unknown_email = auth_client.post(
        "/api/v1/auth/login",
        json={"email": f"unknown-{uuid4().hex}@example.com", "password": "wrong"},
    )

    assert wrong_password.status_code == 401
    assert unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()
    assert wrong_password.headers["www-authenticate"] == "Bearer"
    assert unknown_email.headers["www-authenticate"] == "Bearer"


def test_missing_and_invalid_bearer_tokens_return_a_challenge(
    auth_client: TestClient,
) -> None:
    missing_token = auth_client.get("/api/v1/auth/me")
    invalid_token = auth_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer invalid-token"},
    )

    for response in (missing_token, invalid_token):
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
        assert response.json()["error"]["code"] == "unauthorized"


def test_disabled_user_is_rejected_even_with_a_valid_token(
    auth_client: TestClient,
    database_engine: Engine,
) -> None:
    prefix = f"disabled-{uuid4().hex}"
    registration = register(auth_client, prefix)
    token = str(login(auth_client, f"{prefix}@example.com")["access_token"])

    with database_engine.begin() as connection:
        connection.execute(
            update(User)
            .where(User.id == registration["user"]["id"])
            .values(status=UserStatus.DISABLED)
        )

    response = auth_client.get(
        "/api/v1/auth/me",
        headers=bearer_headers(token),
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_organization_context_is_tenant_scoped_and_role_checked(
    auth_client: TestClient,
    database_engine: Engine,
) -> None:
    admin_prefix = f"admin-{uuid4().hex}"
    agent_prefix = f"agent-{uuid4().hex}"
    admin_registration = register(auth_client, admin_prefix)
    agent_registration = register(auth_client, agent_prefix)
    admin_token = str(login(auth_client, f"{admin_prefix}@example.com")["access_token"])
    agent_token = str(login(auth_client, f"{agent_prefix}@example.com")["access_token"])
    admin_organization_id = str(admin_registration["organization"]["id"])

    with Session(database_engine) as session:
        session.add(
            OrganizationMember(
                organization_id=admin_registration["organization"]["id"],
                user_id=agent_registration["user"]["id"],
                role=MembershipRole.AGENT,
            )
        )
        session.commit()

    admin_context = auth_client.get(
        "/__test__/admin-context",
        headers=bearer_headers(admin_token, admin_organization_id),
    )
    agent_context = auth_client.get(
        "/__test__/organization-context",
        headers=bearer_headers(agent_token, admin_organization_id),
    )
    agent_admin_attempt = auth_client.get(
        "/__test__/admin-context",
        headers=bearer_headers(agent_token, admin_organization_id),
    )
    cross_tenant_attempt = auth_client.get(
        "/__test__/organization-context",
        headers=bearer_headers(
            admin_token,
            str(agent_registration["organization"]["id"]),
        ),
    )

    assert admin_context.status_code == 200
    assert agent_context.status_code == 200
    assert agent_context.json()["role"] == "agent"
    assert agent_admin_attempt.status_code == 403
    assert cross_tenant_attempt.status_code == 404


def test_inactive_membership_and_suspended_organization_are_hidden(
    auth_client: TestClient,
    database_engine: Engine,
) -> None:
    prefix = f"inactive-{uuid4().hex}"
    registration = register(auth_client, prefix)
    token = str(login(auth_client, f"{prefix}@example.com")["access_token"])
    organization_id = str(registration["organization"]["id"])

    with database_engine.begin() as connection:
        connection.execute(
            update(OrganizationMember)
            .where(
                OrganizationMember.organization_id
                == registration["organization"]["id"],
                OrganizationMember.user_id == registration["user"]["id"],
            )
            .values(status=MembershipStatus.INACTIVE)
        )

    inactive_response = auth_client.get(
        "/__test__/organization-context",
        headers=bearer_headers(token, organization_id),
    )
    assert inactive_response.status_code == 404

    with database_engine.begin() as connection:
        connection.execute(
            update(OrganizationMember)
            .where(
                OrganizationMember.organization_id
                == registration["organization"]["id"],
                OrganizationMember.user_id == registration["user"]["id"],
            )
            .values(status=MembershipStatus.ACTIVE)
        )
        connection.execute(
            update(Organization)
            .where(Organization.id == registration["organization"]["id"])
            .values(status=OrganizationStatus.SUSPENDED)
        )

    suspended_response = auth_client.get(
        "/__test__/organization-context",
        headers=bearer_headers(token, organization_id),
    )
    assert suspended_response.status_code == 404


def test_missing_organization_header_uses_validation_envelope(
    auth_client: TestClient,
) -> None:
    prefix = f"missing-header-{uuid4().hex}"
    register(auth_client, prefix)
    token = str(login(auth_client, f"{prefix}@example.com")["access_token"])

    response = auth_client.get(
        "/__test__/organization-context",
        headers=bearer_headers(token),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
