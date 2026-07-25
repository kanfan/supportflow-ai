from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import (
    OrganizationContext,
    get_database_session,
    require_roles,
)
from app.identity.models import MembershipRole, OrganizationMember
from app.identity.schemas import (
    OrganizationMemberCreateRequest,
    OrganizationMemberListResponse,
    OrganizationMemberResponse,
    PaginationResponse,
)
from app.identity.service import (
    MembershipConflictError,
    MembershipUserNotFoundError,
    OrganizationMembershipService,
)


router = APIRouter(
    prefix="/api/v1/organization-members",
    tags=["organization-members"],
)
require_admin = require_roles(MembershipRole.ADMIN)


def member_response(member: OrganizationMember) -> OrganizationMemberResponse:
    return OrganizationMemberResponse(
        user_id=member.user_id,
        email=member.user.email,
        role=member.role,
        status=member.status,
        created_at=member.created_at,
        updated_at=member.updated_at,
    )


@router.get("", response_model=OrganizationMemberListResponse)
def list_organization_members(
    context: Annotated[OrganizationContext, Depends(require_admin)],
    session: Annotated[Session, Depends(get_database_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> OrganizationMemberListResponse:
    service = OrganizationMembershipService(session)
    page = service.list_members(
        organization_id=context.organization.id,
        limit=limit,
        offset=offset,
    )
    return OrganizationMemberListResponse(
        items=[member_response(member) for member in page.items],
        pagination=PaginationResponse(
            limit=limit,
            offset=offset,
            total=page.total,
        ),
    )


@router.post(
    "",
    response_model=OrganizationMemberResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_organization_member(
    request: OrganizationMemberCreateRequest,
    context: Annotated[OrganizationContext, Depends(require_admin)],
    session: Annotated[Session, Depends(get_database_session)],
) -> OrganizationMemberResponse:
    service = OrganizationMembershipService(session)
    try:
        membership = service.add_agent(
            organization_id=context.organization.id,
            actor_user_id=context.membership.user_id,
            normalized_email=str(request.email),
        )
    except MembershipUserNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        ) from None
    except MembershipConflictError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Organization membership already exists",
        ) from None

    return member_response(membership)
