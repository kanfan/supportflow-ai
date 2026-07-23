from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.identity.models import (
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from app.infrastructure.database import Base


class TicketStatus(StrEnum):
    OPEN = "open"
    PROCESSING = "processing"
    WAITING_FOR_AGENT = "waiting_for_agent"
    RESOLVED = "resolved"
    CLOSED = "closed"


class TicketSourceType(StrEnum):
    MANUAL = "manual"
    API = "api"


class MessageAuthorType(StrEnum):
    CUSTOMER = "customer"
    AGENT = "agent"
    SYSTEM = "system"


ticket_status_type = Enum(
    TicketStatus,
    name="ticket_status",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
    values_callable=lambda members: [member.value for member in members],
)
ticket_source_type = Enum(
    TicketSourceType,
    name="ticket_source_type",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
    values_callable=lambda members: [member.value for member in members],
)
message_author_type = Enum(
    MessageAuthorType,
    name="message_author_type",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
    values_callable=lambda members: [member.value for member in members],
)


class Customer(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "customers"
    __table_args__ = (
        CheckConstraint("name = trim(name) AND name <> ''", name="name_nonempty"),
        CheckConstraint(
            "email IS NULL OR email = lower(email)",
            name="email_lowercase",
        ),
        UniqueConstraint(
            "organization_id",
            "id",
            name="uq_customers_organization_id_id",
        ),
        UniqueConstraint(
            "organization_id",
            "external_id",
            name="uq_customers_organization_id_external_id",
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320))
    external_id: Mapped[str | None] = mapped_column(String(255))


class Ticket(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tickets"
    __table_args__ = (
        CheckConstraint(
            "subject = trim(subject) AND subject <> ''",
            name="subject_nonempty",
        ),
        UniqueConstraint(
            "organization_id",
            "id",
            name="uq_tickets_organization_id_id",
        ),
        UniqueConstraint(
            "organization_id",
            "id",
            "customer_id",
            name="uq_tickets_organization_id_id_customer_id",
        ),
        UniqueConstraint(
            "organization_id",
            "source_type",
            "external_id",
            name="uq_tickets_organization_id_source_type_external_id",
        ),
        ForeignKeyConstraint(
            ["organization_id", "customer_id"],
            ["customers.organization_id", "customers.id"],
            name="fk_tickets_organization_id_customers",
        ),
        Index(
            "ix_tickets_organization_created_id",
            "organization_id",
            "created_at",
            "id",
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"),
        nullable=False,
    )
    customer_id: Mapped[UUID | None] = mapped_column(nullable=True)
    source_type: Mapped[TicketSourceType] = mapped_column(
        ticket_source_type,
        default=TicketSourceType.MANUAL,
    )
    external_id: Mapped[str | None] = mapped_column(String(255))
    subject: Mapped[str] = mapped_column(String(300))
    status: Mapped[TicketStatus] = mapped_column(
        ticket_status_type,
        default=TicketStatus.OPEN,
    )


class TicketMessage(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "ticket_messages"
    __table_args__ = (
        CheckConstraint("body = trim(body) AND body <> ''", name="body_nonempty"),
        CheckConstraint(
            "("
            "author_type = 'customer' AND author_user_id IS NULL "
            "AND author_customer_id IS NOT NULL"
            ") OR ("
            "author_type = 'agent' AND author_user_id IS NOT NULL "
            "AND author_customer_id IS NULL"
            ") OR ("
            "author_type = 'system' AND author_user_id IS NULL "
            "AND author_customer_id IS NULL"
            ")",
            name="valid_author_identity",
        ),
        ForeignKeyConstraint(
            ["organization_id", "ticket_id"],
            ["tickets.organization_id", "tickets.id"],
            name="fk_ticket_messages_organization_id_tickets",
        ),
        ForeignKeyConstraint(
            ["organization_id", "author_customer_id"],
            ["customers.organization_id", "customers.id"],
            name="fk_ticket_messages_organization_id_customers",
        ),
        ForeignKeyConstraint(
            ["organization_id", "author_user_id"],
            [
                "organization_members.organization_id",
                "organization_members.user_id",
            ],
            name="fk_ticket_messages_organization_id_organization_members",
        ),
        ForeignKeyConstraint(
            ["organization_id", "ticket_id", "author_customer_id"],
            ["tickets.organization_id", "tickets.id", "tickets.customer_id"],
            name="fk_ticket_messages_ticket_customer_matches_author_tickets",
        ),
        Index(
            "ix_ticket_messages_organization_ticket_created",
            "organization_id",
            "ticket_id",
            "created_at",
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"),
        nullable=False,
    )
    ticket_id: Mapped[UUID] = mapped_column(nullable=False)
    author_type: Mapped[MessageAuthorType] = mapped_column(message_author_type)
    author_user_id: Mapped[UUID | None] = mapped_column(nullable=True)
    author_customer_id: Mapped[UUID | None] = mapped_column(nullable=True)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
