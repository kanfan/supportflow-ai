from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    desc,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.identity.models import TimestampMixin, UUIDPrimaryKeyMixin
from app.infrastructure.database import Base


MAX_DOCUMENT_BYTES = 10 * 1024 * 1024


class DocumentSourceType(StrEnum):
    UPLOAD = "upload"


class DocumentMediaType(StrEnum):
    PDF = "application/pdf"
    TEXT = "text/plain"
    MARKDOWN = "text/markdown"


class DocumentProcessingStatus(StrEnum):
    QUEUED = "queued"
    EXTRACTING = "extracting"
    READY = "ready"
    FAILED = "failed"


class DocumentErrorCode(StrEnum):
    DISPATCH_FAILED = "dispatch_failed"
    STORAGE_UNAVAILABLE = "storage_unavailable"
    SCANNER_UNAVAILABLE = "scanner_unavailable"
    MALWARE_DETECTED = "malware_detected"
    UNSUPPORTED_DOCUMENT = "unsupported_document"
    EXTRACTION_FAILED = "extraction_failed"
    EXTRACTION_LIMIT_EXCEEDED = "extraction_limit_exceeded"
    RETRY_EXHAUSTED = "retry_exhausted"


document_source_type = Enum(
    DocumentSourceType,
    name="document_source_type",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
    values_callable=lambda members: [member.value for member in members],
)
document_media_type = Enum(
    DocumentMediaType,
    name="document_media_type",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
    values_callable=lambda members: [member.value for member in members],
)
document_processing_status = Enum(
    DocumentProcessingStatus,
    name="document_processing_status",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
    values_callable=lambda members: [member.value for member in members],
)


class Document(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "display_filename = trim(display_filename) AND display_filename <> ''",
            name="display_filename_nonempty",
        ),
        UniqueConstraint(
            "organization_id",
            "id",
            name="uq_documents_organization_id_id",
        ),
        UniqueConstraint(
            "organization_id",
            "source_type",
            "external_id",
            name="uq_documents_organization_id_source_type_external_id",
        ),
        ForeignKeyConstraint(
            ["organization_id", "created_by_user_id"],
            [
                "organization_members.organization_id",
                "organization_members.user_id",
            ],
            name="fk_documents_organization_id_organization_members",
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"),
        nullable=False,
    )
    source_type: Mapped[DocumentSourceType] = mapped_column(
        document_source_type,
        default=DocumentSourceType.UPLOAD,
    )
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_filename: Mapped[str] = mapped_column(String(200))
    created_by_user_id: Mapped[UUID] = mapped_column(nullable=False)

    versions: Mapped[list[DocumentVersion]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
    )


class DocumentVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        CheckConstraint("version_number > 0", name="version_number_positive"),
        CheckConstraint(
            f"size_bytes > 0 AND size_bytes <= {MAX_DOCUMENT_BYTES}",
            name="size_bytes_valid",
        ),
        CheckConstraint(
            r"content_sha256 ~ '^[0-9a-f]{64}$'",
            name="content_sha256_lowercase_hex",
        ),
        CheckConstraint(
            "storage_key = trim(storage_key) AND storage_key <> ''",
            name="storage_key_nonempty",
        ),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        ForeignKeyConstraint(
            ["organization_id", "document_id"],
            ["documents.organization_id", "documents.id"],
            name="fk_document_versions_organization_id_documents",
        ),
        UniqueConstraint(
            "organization_id",
            "id",
            name="uq_document_versions_organization_id_id",
        ),
        UniqueConstraint(
            "organization_id",
            "document_id",
            "version_number",
            name="uq_document_versions_organization_document_version",
        ),
        UniqueConstraint("storage_key", name="uq_document_versions_storage_key"),
        Index(
            "ix_document_versions_organization_document_version",
            "organization_id",
            "document_id",
            desc("version_number"),
        ),
        Index(
            "ix_document_versions_organization_status_updated_id",
            "organization_id",
            "status",
            desc("updated_at"),
            desc("id"),
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"),
        nullable=False,
    )
    document_id: Mapped[UUID] = mapped_column(nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, default=1)
    media_type: Mapped[DocumentMediaType] = mapped_column(document_media_type)
    size_bytes: Mapped[int] = mapped_column(Integer)
    content_sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(500))
    status: Mapped[DocumentProcessingStatus] = mapped_column(
        document_processing_status,
        default=DocumentProcessingStatus.QUEUED,
    )
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    processing_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)

    document: Mapped[Document] = relationship(back_populates="versions")
