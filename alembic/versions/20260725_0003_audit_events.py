"""Create tenant-scoped append-only audit events.

Revision ID: 0003_audit_events
Revises: 0002_ticket
Create Date: 2026-07-25
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0003_audit_events"
down_revision: str | None = "0002_ticket"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("resource_type", sa.String(length=100), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action = lower(action) "
            r"AND action ~ '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$'",
            name=op.f("ck_audit_events_action_lowercase_dotted"),
        ),
        sa.CheckConstraint(
            "resource_type = lower(resource_type) "
            "AND resource_type ~ '^[a-z][a-z0-9_]*$'",
            name=op.f("ck_audit_events_resource_type_lowercase"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(metadata) = 'object'",
            name=op.f("ck_audit_events_metadata_object"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "actor_user_id"],
            [
                "organization_members.organization_id",
                "organization_members.user_id",
            ],
            name=op.f("fk_audit_events_organization_id_organization_members"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_audit_events_organization_id_organizations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
    )
    op.create_index(
        "ix_audit_events_organization_created_id",
        "audit_events",
        ["organization_id", "created_at", "id"],
        unique=False,
    )
    op.execute(
        """
        CREATE FUNCTION prevent_audit_event_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events are append-only';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_events_append_only
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW
        EXECUTE FUNCTION prevent_audit_event_mutation()
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_events_append_only_truncate
        BEFORE TRUNCATE ON audit_events
        FOR EACH STATEMENT
        EXECUTE FUNCTION prevent_audit_event_mutation()
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_audit_events_organization_created_id",
        table_name="audit_events",
    )
    op.drop_table("audit_events")
    op.execute("DROP FUNCTION prevent_audit_event_mutation()")
