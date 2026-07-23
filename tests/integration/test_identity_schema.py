from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.identity.models import (
    MembershipRole,
    MembershipStatus,
    Organization,
    OrganizationMember,
    User,
)


pytestmark = pytest.mark.integration


def test_two_organizations_have_independent_memberships(
    database_session: Session,
) -> None:
    first_organization = Organization(name="First Org", slug="first-org")
    second_organization = Organization(name="Second Org", slug="second-org")
    first_user = User(
        email="first@example.com",
        password_hash="not-a-real-password-hash",
    )
    second_user = User(
        email="second@example.com",
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
        status=MembershipStatus.ACTIVE,
    )

    database_session.add_all([first_membership, second_membership])
    database_session.flush()

    memberships = database_session.scalars(
        select(OrganizationMember).order_by(OrganizationMember.organization_id)
    ).all()

    assert len(memberships) == 2
    assert {membership.organization_id for membership in memberships} == {
        first_organization.id,
        second_organization.id,
    }


@pytest.mark.parametrize(
    ("table", "required_values", "invalid_column", "invalid_value"),
    [
        (
            "organizations",
            {"id": uuid4(), "name": "Invalid Org", "slug": "invalid-org"},
            "status",
            "deleted",
        ),
        (
            "users",
            {
                "id": uuid4(),
                "email": "invalid@example.com",
                "password_hash": "not-a-real-password-hash",
            },
            "status",
            "pending",
        ),
    ],
)
def test_invalid_identity_statuses_are_rejected(
    database_session: Session,
    table: str,
    required_values: dict[str, object],
    invalid_column: str,
    invalid_value: str,
) -> None:
    columns = [*required_values, invalid_column]
    placeholders = [f":{column}" for column in columns]
    statement = text(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(placeholders)})"
    )

    with pytest.raises(IntegrityError), database_session.begin_nested():
        database_session.execute(
            statement,
            {**required_values, invalid_column: invalid_value},
        )


@pytest.mark.parametrize(
    ("column", "value"),
    [("role", "owner"), ("status", "pending")],
)
def test_invalid_membership_values_are_rejected(
    database_session: Session,
    column: str,
    value: str,
) -> None:
    organization = Organization(name="Valid Org", slug=f"valid-{uuid4().hex}")
    user = User(
        email=f"{uuid4().hex}@example.com",
        password_hash="not-a-real-password-hash",
    )
    database_session.add_all([organization, user])
    database_session.flush()

    values = {
        "organization_id": organization.id,
        "user_id": user.id,
        "role": MembershipRole.AGENT.value,
        "status": MembershipStatus.ACTIVE.value,
        column: value,
    }
    statement = text(
        "INSERT INTO organization_members "
        "(organization_id, user_id, role, status) "
        "VALUES (:organization_id, :user_id, :role, :status)"
    )

    with pytest.raises(IntegrityError), database_session.begin_nested():
        database_session.execute(statement, values)
