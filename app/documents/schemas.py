from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.documents.models import (
    DocumentMediaType,
    DocumentProcessingStatus,
    DocumentSourceType,
)


class DocumentVersionStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    version_number: int
    media_type: DocumentMediaType
    size_bytes: int
    status: DocumentProcessingStatus
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class DocumentStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    source_type: DocumentSourceType
    display_filename: str
    created_at: datetime
    updated_at: datetime
    version: DocumentVersionStatusResponse
