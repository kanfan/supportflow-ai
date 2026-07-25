from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.dependencies import (
    OrganizationContext,
    get_database_session,
    require_roles,
)
from app.audit.models import AuditAction, AuditEvent, AuditResourceType
from app.audit.schemas import AuditEventListResponse, AuditEventResponse
from app.audit.service import AuditEventService
from app.identity.models import MembershipRole
from app.identity.schemas import PaginationResponse


router = APIRouter(prefix="/api/v1/audit-events", tags=["audit-events"])
require_admin = require_roles(MembershipRole.ADMIN)


def audit_event_response(event: AuditEvent) -> AuditEventResponse:
    return AuditEventResponse(
        id=event.id,
        actor_user_id=event.actor_user_id,
        action=AuditAction(event.action),
        resource_type=AuditResourceType(event.resource_type),
        resource_id=event.resource_id,
        metadata=event.event_metadata,
        created_at=event.created_at,
    )


@router.get("", response_model=AuditEventListResponse)
def list_audit_events(
    context: Annotated[OrganizationContext, Depends(require_admin)],
    session: Annotated[Session, Depends(get_database_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditEventListResponse:
    page = AuditEventService(
        session,
        context.organization.id,
    ).list_events(limit=limit, offset=offset)
    return AuditEventListResponse(
        items=[audit_event_response(event) for event in page.items],
        pagination=PaginationResponse(
            limit=limit,
            offset=offset,
            total=page.total,
        ),
    )
