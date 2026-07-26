from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.audit.models import AuditAction, AuditResourceType
from app.identity.schemas import PaginationResponse


class AuditEventResponse(BaseModel):
    id: UUID
    actor_user_id: UUID | None
    action: AuditAction
    resource_type: AuditResourceType
    resource_id: UUID | None
    metadata: dict[str, str]
    created_at: datetime


class AuditEventListResponse(BaseModel):
    items: list[AuditEventResponse]
    pagination: PaginationResponse
