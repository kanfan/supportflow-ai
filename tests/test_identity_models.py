from sqlalchemy import CheckConstraint, Enum, UniqueConstraint

from app.identity.models import (
    MembershipRole,
    MembershipStatus,
    OrganizationStatus,
    UserStatus,
)
from app.infrastructure.database import Base


def enum_values(table_name: str, column_name: str) -> set[str]:
    column = Base.metadata.tables[table_name].columns[column_name]
    assert isinstance(column.type, Enum)
    return set(column.type.enums)


def test_identity_tables_are_registered() -> None:
    assert {
        "organization_members",
        "organizations",
        "users",
    } <= set(Base.metadata.tables)


def test_membership_uses_composite_primary_key_and_mutable_timestamps() -> None:
    table = Base.metadata.tables["organization_members"]

    assert [column.name for column in table.primary_key.columns] == [
        "organization_id",
        "user_id",
    ]
    assert table.columns.created_at.server_default is not None
    assert table.columns.updated_at.server_default is not None
    assert table.columns.updated_at.onupdate is not None


def test_identity_status_and_role_values_match_the_adr() -> None:
    assert enum_values("organizations", "status") == {
        status.value for status in OrganizationStatus
    }
    assert enum_values("users", "status") == {status.value for status in UserStatus}
    assert enum_values("organization_members", "role") == {
        role.value for role in MembershipRole
    }
    assert enum_values("organization_members", "status") == {
        status.value for status in MembershipStatus
    }


def test_email_and_slug_are_unique_and_normalized() -> None:
    organization_constraints = Base.metadata.tables["organizations"].constraints
    user_constraints = Base.metadata.tables["users"].constraints

    assert any(
        isinstance(constraint, UniqueConstraint)
        and [column.name for column in constraint.columns] == ["slug"]
        for constraint in organization_constraints
    )
    assert any(
        isinstance(constraint, CheckConstraint)
        and str(constraint.sqltext) == "slug = lower(slug)"
        for constraint in organization_constraints
    )
    assert any(
        isinstance(constraint, UniqueConstraint)
        and [column.name for column in constraint.columns] == ["email"]
        for constraint in user_constraints
    )
    assert any(
        isinstance(constraint, CheckConstraint)
        and str(constraint.sqltext) == "email = lower(email)"
        for constraint in user_constraints
    )
