from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import logging
from typing import BinaryIO
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.audit.service import AuditEventService
from app.documents.models import (
    Document,
    DocumentErrorCode,
    DocumentProcessingStatus,
    DocumentSourceType,
    DocumentVersion,
)
from app.documents.ports import DocumentStorage, DocumentTaskDispatcher
from app.documents.repository import DocumentRepository, DocumentWithVersion
from app.documents.outbox import stage_upload_with_intent
from app.documents.validation import stage_document_upload


logger = logging.getLogger(__name__)


class DocumentLogCategory(StrEnum):
    STORAGE_UNAVAILABLE = "storage_unavailable"
    STORAGE_CLEANUP_FAILED = "storage_cleanup_failed"
    DISPATCH_FAILED = "dispatch_failed"
    DISPATCH_COMPENSATION_FAILED = "dispatch_compensation_failed"


class DocumentNotFoundError(LookupError):
    """Raised when a document is missing from the selected organization."""


class DocumentStorageUnavailableError(RuntimeError):
    """Raised when private object storage cannot accept an upload."""


class DocumentPersistenceError(RuntimeError):
    """Raised when document metadata cannot be committed."""


class DocumentDispatchError(RuntimeError):
    """Raised after durable state records that broker publication failed."""

    def __init__(self, document_id: UUID) -> None:
        self.document_id = document_id
        super().__init__("Document processing could not be queued")


@dataclass(frozen=True)
class CreatedDocument:
    document: Document
    version: DocumentVersion


def build_storage_key(
    *,
    organization_id: UUID,
    document_id: UUID,
    version_id: UUID,
) -> str:
    return (
        f"organizations/{organization_id}/documents/{document_id}/versions/{version_id}"
    )


class DocumentService:
    def __init__(
        self,
        session: Session,
        organization_id: UUID,
        *,
        storage: DocumentStorage,
        dispatcher: DocumentTaskDispatcher,
        max_upload_bytes: int,
    ) -> None:
        self._session = session
        self._organization_id = organization_id
        self._storage = storage
        self._dispatcher = dispatcher
        self._max_upload_bytes = max_upload_bytes
        self._repository = DocumentRepository(session, organization_id)
        self._audit = AuditEventService(session, organization_id)

    def create_upload(
        self,
        *,
        filename: str | None,
        source: BinaryIO,
        actor_user_id: UUID,
    ) -> CreatedDocument:
        staged = stage_document_upload(
            filename=filename,
            source=source,
            max_bytes=self._max_upload_bytes,
        )
        document_id = uuid4()
        version_id = uuid4()
        storage_key = build_storage_key(
            organization_id=self._organization_id,
            document_id=document_id,
            version_id=version_id,
        )

        try:
            try:
                self._storage.put(storage_key, staged.stream)
            except Exception as exc:
                logger.warning(
                    "document_storage_put_failed organization_id=%s "
                    "document_id=%s version_id=%s error_category=%s",
                    self._organization_id,
                    document_id,
                    version_id,
                    DocumentLogCategory.STORAGE_UNAVAILABLE,
                )
                raise DocumentStorageUnavailableError from exc

            document = Document(
                id=document_id,
                organization_id=self._organization_id,
                source_type=DocumentSourceType.UPLOAD,
                external_id=None,
                display_filename=staged.display_filename,
                created_by_user_id=actor_user_id,
            )
            version = DocumentVersion(
                id=version_id,
                organization_id=self._organization_id,
                document_id=document_id,
                version_number=1,
                media_type=staged.media_type,
                size_bytes=staged.size_bytes,
                content_sha256=staged.content_sha256,
                storage_key=storage_key,
                status=DocumentProcessingStatus.QUEUED,
                attempt_count=0,
            )
            try:
                stage_upload_with_intent(
                    self._session,
                    self._organization_id,
                    document=document,
                    version=version,
                )
                self._session.commit()
            except Exception as exc:
                self._session.rollback()
                self._delete_after_failed_persistence(
                    storage_key=storage_key,
                    document_id=document_id,
                    version_id=version_id,
                )
                raise DocumentPersistenceError from exc

            # The relay owns broker I/O. A durable commit is sufficient for 202.
            return CreatedDocument(document=document, version=version)
        finally:
            staged.close()

    def get_status(self, document_id: UUID) -> DocumentWithVersion:
        result = self._repository.get_with_latest_version(document_id)
        if result is None:
            raise DocumentNotFoundError
        return result

    def _delete_after_failed_persistence(
        self,
        *,
        storage_key: str,
        document_id: UUID,
        version_id: UUID,
    ) -> None:
        try:
            self._storage.delete(storage_key)
        except Exception:
            logger.warning(
                "document_storage_cleanup_failed organization_id=%s "
                "document_id=%s version_id=%s error_category=%s",
                self._organization_id,
                document_id,
                version_id,
                DocumentLogCategory.STORAGE_CLEANUP_FAILED,
            )

    def _record_dispatch_failure(
        self,
        *,
        document: Document,
        version: DocumentVersion,
    ) -> None:
        version.status = DocumentProcessingStatus.FAILED
        version.error_code = DocumentErrorCode.DISPATCH_FAILED.value
        try:
            self._audit.record_document_failed(
                document_version_id=version.id,
                version_number=version.version_number,
                error_code=DocumentErrorCode.DISPATCH_FAILED,
                attempt_count=version.attempt_count,
            )
            self._session.commit()
        except Exception:
            self._session.rollback()
            logger.error(
                "document_dispatch_compensation_failed organization_id=%s "
                "document_id=%s version_id=%s error_category=%s",
                self._organization_id,
                document.id,
                version.id,
                DocumentLogCategory.DISPATCH_COMPENSATION_FAILED,
            )
            raise
