"""R1 transaction foundation, deliberately not wired into the upload API yet."""

from uuid import UUID, uuid4

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.audit.service import AuditEventService
from app.documents.models import (
    Document,
    DocumentIngestionIntent,
    DocumentProcessingStatus,
    DocumentVersion,
)


class IngestionIntentRepository:
    def __init__(self, session: Session, organization_id: UUID) -> None:
        self._session = session
        self._organization_id = organization_id

    def add(self, *, version_id: UUID, task_id: str) -> DocumentIngestionIntent:
        if not task_id or task_id != task_id.strip() or len(task_id) > 255:
            raise ValueError("Invalid ingestion task identity")
        intent = DocumentIngestionIntent(
            organization_id=self._organization_id,
            document_version_id=version_id,
            task_id=task_id,
        )
        self._session.add(intent)
        return intent

    def get_for_version(self, version_id: UUID) -> DocumentIngestionIntent | None:
        return self._session.scalar(
            select(DocumentIngestionIntent).where(
                DocumentIngestionIntent.organization_id == self._organization_id,
                DocumentIngestionIntent.document_version_id == version_id,
            )
        )


def stage_upload_with_intent(
    session: Session,
    organization_id: UUID,
    *,
    document: Document,
    version: DocumentVersion,
) -> DocumentIngestionIntent:
    """Flush document/version/audit/intent in the caller's transaction.

    The caller MUST commit on success or roll back on any failure. This helper
    owns neither transaction completion nor storage/broker I/O. It is for new
    uploads, not backfill or retry; duplicate intent insertion fails closed.
    """
    if (
        document.organization_id != organization_id
        or version.organization_id != organization_id
        or document.id is None
        or version.id is None
        or version.document_id != document.id
        or version.status != DocumentProcessingStatus.QUEUED
        or version.processing_task_id is not None
        or version.attempt_count != 0
        or not inspect(document).transient
        or not inspect(version).transient
    ):
        raise ValueError("Expected new tenant-matched queued upload")

    session.add_all([document, version])
    # Flush parents before the independent FK-only outbox mapping is inserted.
    session.flush()
    AuditEventService(session, organization_id).record_document_uploaded(
        actor_user_id=document.created_by_user_id,
        document_id=document.id,
        version_number=version.version_number,
        media_type=version.media_type,
        size_bytes=version.size_bytes,
    )
    intent = IngestionIntentRepository(session, organization_id).add(
        version_id=version.id, task_id=str(uuid4())
    )
    session.flush()
    return intent
