"""Create tenant-scoped documents and document versions.

Revision ID: 0004_documents
Revises: 0003_audit_events
Create Date: 2026-07-30
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0004_documents"
down_revision: str | None = "0003_audit_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column(
            "source_type",
            sa.Enum(
                "upload",
                name="document_source_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("display_filename", sa.String(length=200), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
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
            "display_filename = trim(display_filename) AND display_filename <> ''",
            name=op.f("ck_documents_display_filename_nonempty"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "created_by_user_id"],
            [
                "organization_members.organization_id",
                "organization_members.user_id",
            ],
            name=op.f("fk_documents_organization_id_organization_members"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_documents_organization_id_organizations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
        sa.UniqueConstraint(
            "organization_id",
            "id",
            name=op.f("uq_documents_organization_id_id"),
        ),
        sa.UniqueConstraint(
            "organization_id",
            "source_type",
            "external_id",
            name=op.f("uq_documents_organization_id_source_type_external_id"),
        ),
    )
    op.create_table(
        "document_versions",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column(
            "media_type",
            sa.Enum(
                "application/pdf",
                "text/plain",
                "text/markdown",
                name="document_media_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "queued",
                "extracting",
                "ready",
                "failed",
                name="document_processing_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("processing_task_id", sa.String(length=255), nullable=True),
        sa.Column(
            "processing_started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("error_code", sa.String(length=100), nullable=True),
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
            "attempt_count >= 0",
            name=op.f("ck_document_versions_attempt_count_nonnegative"),
        ),
        sa.CheckConstraint(
            r"content_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_document_versions_content_sha256_lowercase_hex"),
        ),
        sa.CheckConstraint(
            "size_bytes > 0 AND size_bytes <= 10485760",
            name=op.f("ck_document_versions_size_bytes_valid"),
        ),
        sa.CheckConstraint(
            "storage_key = trim(storage_key) AND storage_key <> ''",
            name=op.f("ck_document_versions_storage_key_nonempty"),
        ),
        sa.CheckConstraint(
            "version_number > 0",
            name=op.f("ck_document_versions_version_number_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "document_id"],
            ["documents.organization_id", "documents.id"],
            name=op.f("fk_document_versions_organization_id_documents"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_document_versions_organization_id_organizations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_versions")),
        sa.UniqueConstraint(
            "organization_id",
            "document_id",
            "version_number",
            name=op.f("uq_document_versions_organization_document_version"),
        ),
        sa.UniqueConstraint(
            "organization_id",
            "id",
            name=op.f("uq_document_versions_organization_id_id"),
        ),
        sa.UniqueConstraint(
            "storage_key",
            name=op.f("uq_document_versions_storage_key"),
        ),
    )
    op.create_index(
        "ix_document_versions_organization_document_version",
        "document_versions",
        ["organization_id", "document_id", sa.text("version_number DESC")],
        unique=False,
    )
    op.create_index(
        "ix_document_versions_organization_status_updated_id",
        "document_versions",
        [
            "organization_id",
            "status",
            sa.text("updated_at DESC"),
            sa.text("id DESC"),
        ],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_document_versions_organization_status_updated_id",
        table_name="document_versions",
    )
    op.drop_index(
        "ix_document_versions_organization_document_version",
        table_name="document_versions",
    )
    op.drop_table("document_versions")
    op.drop_table("documents")
