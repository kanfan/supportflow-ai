"""Add immutable ingestion intent identities (no producer switch).

Revision ID: 0005_ingestion_outbox
Revises: 0004_documents
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0005_ingestion_outbox"
down_revision: str | None = "0004_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document_ingestion_outbox",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document_ingestion_outbox"),
        sa.ForeignKeyConstraint(
            ["organization_id", "document_version_id"],
            ["document_versions.organization_id", "document_versions.id"],
            name="fk_ingestion_outbox_tenant_version",
        ),
        sa.UniqueConstraint(
            "organization_id", "document_version_id", name="uq_ingestion_outbox_version"
        ),
        sa.UniqueConstraint("task_id", name="uq_ingestion_outbox_task_id"),
        sa.CheckConstraint(
            "task_id = trim(task_id) AND task_id <> ''",
            name=op.f("ck_document_ingestion_outbox_task_id_nonempty"),
        ),
    )
    op.execute("""
        CREATE FUNCTION ingestion_outbox_identity_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF ROW(NEW.id, NEW.organization_id, NEW.document_version_id,
                   NEW.task_id, NEW.created_at)
                IS DISTINCT FROM
               ROW(OLD.id, OLD.organization_id, OLD.document_version_id,
                   OLD.task_id, OLD.created_at) THEN
                RAISE EXCEPTION 'Ingestion intent identity is immutable';
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER ingestion_outbox_identity_immutable
        BEFORE UPDATE ON document_ingestion_outbox
        FOR EACH ROW EXECUTE FUNCTION ingestion_outbox_identity_guard()
    """)


def downgrade() -> None:
    # This foundation has no settled-state lifecycle yet: every row is retained.
    # Lock out concurrent insertions before checking, within Alembic's transaction.
    op.execute("LOCK TABLE document_ingestion_outbox IN ACCESS EXCLUSIVE MODE")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM document_ingestion_outbox) THEN
                RAISE EXCEPTION 'Cannot downgrade while ingestion intents remain';
            END IF;
        END $$
    """)
    op.drop_table("document_ingestion_outbox")
    op.execute("DROP FUNCTION ingestion_outbox_identity_guard()")
