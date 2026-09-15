"""Add relay lifecycle; refuse rollback while any durable intents remain."""

from alembic import op
import sqlalchemy as sa

revision = "0006_outbox_lifecycle"
down_revision = "0005_ingestion_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "document_ingestion_outbox",
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    for name in ("published_at", "settled_at", "unresolved_at", "lease_expires_at"):
        op.add_column(
            "document_ingestion_outbox", sa.Column(name, sa.DateTime(timezone=True))
        )
    op.add_column("document_ingestion_outbox", sa.Column("lease_token", sa.Uuid()))
    for name in ("publish_attempts", "recovery_attempts"):
        op.add_column(
            "document_ingestion_outbox",
            sa.Column(name, sa.Integer(), nullable=False, server_default=sa.text("0")),
        )
    op.add_column("document_ingestion_outbox", sa.Column("last_error", sa.String(32)))
    op.create_check_constraint(
        "attempts_nonnegative",
        "document_ingestion_outbox",
        "publish_attempts >= 0 AND recovery_attempts >= 0",
    )
    op.create_check_constraint(
        "lease_pair",
        "document_ingestion_outbox",
        "(lease_token IS NULL) = (lease_expires_at IS NULL)",
    )
    op.create_check_constraint(
        "safe_error",
        "document_ingestion_outbox",
        "last_error IS NULL OR last_error IN ('publish_unavailable', 'attempts_exhausted', 'ownership_ambiguous')",
    )
    op.create_index(
        "ix_ingestion_outbox_due",
        "document_ingestion_outbox",
        ["available_at", "id"],
        postgresql_where=sa.text("settled_at IS NULL AND unresolved_at IS NULL"),
    )
    op.create_index(
        "ix_ingestion_outbox_retention",
        "document_ingestion_outbox",
        ["settled_at", "id"],
        postgresql_where=sa.text("settled_at IS NOT NULL AND unresolved_at IS NULL"),
    )


def downgrade() -> None:
    op.execute("LOCK TABLE document_ingestion_outbox IN ACCESS EXCLUSIVE MODE")
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM document_ingestion_outbox) THEN RAISE EXCEPTION 'Cannot downgrade while ingestion intents remain'; END IF; END $$"
    )
    op.drop_index(
        "ix_ingestion_outbox_retention", table_name="document_ingestion_outbox"
    )
    op.drop_index("ix_ingestion_outbox_due", table_name="document_ingestion_outbox")
    for name in ("safe_error", "lease_pair", "attempts_nonnegative"):
        op.drop_constraint(
            op.f(f"ck_document_ingestion_outbox_{name}"),
            "document_ingestion_outbox",
            type_="check",
        )
    for name in (
        "last_error",
        "publish_attempts",
        "recovery_attempts",
        "lease_token",
        "lease_expires_at",
        "published_at",
        "settled_at",
        "unresolved_at",
        "available_at",
    ):
        op.drop_column("document_ingestion_outbox", name)
