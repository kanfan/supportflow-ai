from sqlalchemy import CheckConstraint, Enum, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.orm import configure_mappers

from app.infrastructure.database import Base
from app.tickets.models import (
    MessageAuthorType,
    TicketSourceType,
    TicketStatus,
)


def enum_values(table_name: str, column_name: str) -> set[str]:
    column = Base.metadata.tables[table_name].columns[column_name]
    assert isinstance(column.type, Enum)
    return set(column.type.enums)


def constraint_names(table_name: str, constraint_type: type[object]) -> set[str]:
    names: set[str] = set()
    for constraint in Base.metadata.tables[table_name].constraints:
        if isinstance(constraint, constraint_type) and constraint.name is not None:
            names.add(str(constraint.name))
    return names


def test_ticket_tables_and_mappers_are_registered() -> None:
    configure_mappers()

    assert {"customers", "tickets", "ticket_messages"} <= set(Base.metadata.tables)


def test_ticket_closed_values_match_the_adr() -> None:
    assert enum_values("tickets", "status") == {status.value for status in TicketStatus}
    assert enum_values("tickets", "source_type") == {
        source.value for source in TicketSourceType
    }
    assert enum_values("ticket_messages", "author_type") == {
        author.value for author in MessageAuthorType
    }


def test_tenant_aware_unique_and_foreign_key_constraints_are_present() -> None:
    assert "uq_customers_organization_id_id" in constraint_names(
        "customers", UniqueConstraint
    )
    assert "uq_tickets_organization_id_id" in constraint_names(
        "tickets", UniqueConstraint
    )
    message_foreign_keys = constraint_names(
        "ticket_messages",
        ForeignKeyConstraint,
    )
    assert {
        "fk_ticket_messages_organization_id_customers",
        "fk_ticket_messages_organization_id_organization_members",
        "fk_ticket_messages_organization_id_tickets",
        "fk_ticket_messages_ticket_customer_matches_author_tickets",
    } <= message_foreign_keys


def test_message_author_identity_check_is_present() -> None:
    checks = constraint_names("ticket_messages", CheckConstraint)

    assert "ck_ticket_messages_valid_author_identity" in checks
