from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.dependencies import (
    OrganizationContext,
    get_database_session,
    require_roles,
)
from app.identity.models import MembershipRole, OrganizationMember
from app.identity.schemas import (
    OrganizationMemberCreateRequest,
    OrganizationMemberResponse,
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


@router.get("", response_model=list[OrganizationMemberResponse])
def list_organization_members(
    context: Annotated[OrganizationContext, Depends(require_admin)],
    session: Annotated[Session, Depends(get_database_session)],
) -> list[OrganizationMemberResponse]:
    service = OrganizationMembershipService(session)
    return [
        member_response(member)
        for member in service.list_members(context.organization.id)
    ]


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
