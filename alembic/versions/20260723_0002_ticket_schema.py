"""Create tenant-scoped customers, tickets, and ticket messages.

Revision ID: 0002_ticket
Revises: 0001_identity
Create Date: 2026-07-23
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0002_ticket"
down_revision: str | None = "0001_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "email IS NULL OR email = lower(email)",
            name=op.f("ck_customers_email_lowercase"),
        ),
        sa.CheckConstraint(
            "name = trim(name) AND name <> ''",
            name=op.f("ck_customers_name_nonempty"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_customers_organization_id_organizations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customers")),
        sa.UniqueConstraint(
            "organization_id",
            "external_id",
            name=op.f("uq_customers_organization_id_external_id"),
        ),
        sa.UniqueConstraint(
            "organization_id",
            "id",
            name=op.f("uq_customers_organization_id_id"),
        ),
    )
    op.create_table(
        "tickets",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("customer_id", sa.Uuid(), nullable=True),
        sa.Column(
            "source_type",
            sa.Enum(
                "manual",
                "api",
                name="ticket_source_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("subject", sa.String(length=300), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "open",
                "processing",
                "waiting_for_agent",
                "resolved",
                "closed",
                name="ticket_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "subject = trim(subject) AND subject <> ''",
            name=op.f("ck_tickets_subject_nonempty"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "customer_id"],
            ["customers.organization_id", "customers.id"],
            name=op.f("fk_tickets_organization_id_customers"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_tickets_organization_id_organizations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tickets")),
        sa.UniqueConstraint(
            "organization_id",
            "id",
            "customer_id",
            name=op.f("uq_tickets_organization_id_id_customer_id"),
        ),
        sa.UniqueConstraint(
            "organization_id",
            "id",
            name=op.f("uq_tickets_organization_id_id"),
        ),
        sa.UniqueConstraint(
            "organization_id",
            "source_type",
            "external_id",
            name=op.f("uq_tickets_organization_id_source_type_external_id"),
        ),
    )
    op.create_index(
        "ix_tickets_organization_created_id",
        "tickets",
        ["organization_id", "created_at", "id"],
        unique=False,
    )
    op.create_table(
        "ticket_messages",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column(
            "author_type",
            sa.Enum(
                "customer",
                "agent",
                "system",
                name="message_author_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("author_user_id", sa.Uuid(), nullable=True),
        sa.Column("author_customer_id", sa.Uuid(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "body = trim(body) AND body <> ''",
            name=op.f("ck_ticket_messages_body_nonempty"),
        ),
        sa.CheckConstraint(
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
            name=op.f("ck_ticket_messages_valid_author_identity"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "author_customer_id"],
            ["customers.organization_id", "customers.id"],
            name=op.f("fk_ticket_messages_organization_id_customers"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "author_user_id"],
            [
                "organization_members.organization_id",
                "organization_members.user_id",
            ],
            name=op.f("fk_ticket_messages_organization_id_organization_members"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "ticket_id"],
            ["tickets.organization_id", "tickets.id"],
            name=op.f("fk_ticket_messages_organization_id_tickets"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "ticket_id", "author_customer_id"],
            ["tickets.organization_id", "tickets.id", "tickets.customer_id"],
            name=op.f("fk_ticket_messages_ticket_customer_matches_author_tickets"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_ticket_messages_organization_id_organizations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ticket_messages")),
    )
    op.create_index(
        "ix_ticket_messages_organization_ticket_created",
        "ticket_messages",
        ["organization_id", "ticket_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ticket_messages_organization_ticket_created",
        table_name="ticket_messages",
    )
    op.drop_table("ticket_messages")
    op.drop_index("ix_tickets_organization_created_id", table_name="tickets")
    op.drop_table("tickets")
    op.drop_table("customers")
