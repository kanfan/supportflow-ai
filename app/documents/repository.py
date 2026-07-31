from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.documents.models import Document, DocumentVersion


@dataclass(frozen=True)
class DocumentWithVersion:
    document: Document
    version: DocumentVersion


class DocumentRepository:
    """Tenant-scoped persistence operations for documents and versions."""

    def __init__(self, session: Session, organization_id: UUID) -> None:
        self._session = session
        self._organization_id = organization_id

    def add(self, document: Document, version: DocumentVersion) -> None:
        self._session.add_all([document, version])

    def get_with_latest_version(
        self,
        document_id: UUID,
    ) -> DocumentWithVersion | None:
        row = self._session.execute(
            select(Document, DocumentVersion)
            .join(
                DocumentVersion,
                (DocumentVersion.organization_id == Document.organization_id)
                & (DocumentVersion.document_id == Document.id),
            )
            .where(
                Document.organization_id == self._organization_id,
                Document.id == document_id,
                DocumentVersion.organization_id == self._organization_id,
            )
            .order_by(DocumentVersion.version_number.desc())
            .limit(1)
        ).one_or_none()
        if row is None:
            return None
        document, version = row
        return DocumentWithVersion(document=document, version=version)
