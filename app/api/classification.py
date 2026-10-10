from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.api.dependencies import (
    OrganizationContext,
    get_database_session,
    require_roles,
)
from app.classification.application import (
    ApplicationClassificationError,
    ClassificationApplication,
    ClassificationView,
)
from app.identity.models import MembershipRole

router = APIRouter(prefix="/api/v1/tickets", tags=["classification"])
classification_context = require_roles(MembershipRole.ADMIN, MembershipRole.AGENT)


class EmptyClassificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


def application(request: Request) -> ClassificationApplication:
    return ClassificationApplication(
        request.app.state.session_factory, request.app.state.classification_runtime
    )


@router.get("/{ticket_id}/classification", response_model=ClassificationView)
def get_classification(
    ticket_id: UUID,
    request: Request,
    context: Annotated[OrganizationContext, Depends(classification_context)],
    session: Annotated[Session, Depends(get_database_session)],
) -> ClassificationView:
    org, actor = context.organization.id, context.membership.user_id
    # Auth dependencies opened this transaction. Never leave it across I/O.
    session.rollback()
    try:
        return application(request).read(org, actor, ticket_id)
    except ApplicationClassificationError as exc:
        raise HTTPException(
            status_code=exc.status,
            detail={
                "code": exc.code,
                "message": "Classification request could not be completed",
            },
        ) from None


@router.post("/{ticket_id}/classification", response_model=ClassificationView)
def classify_ticket(
    ticket_id: UUID,
    request: Request,
    context: Annotated[OrganizationContext, Depends(classification_context)],
    session: Annotated[Session, Depends(get_database_session)],
    body: Annotated[EmptyClassificationRequest | None, Body()] = None,
) -> ClassificationView:
    org, actor = context.organization.id, context.membership.user_id
    session.rollback()
    try:
        return application(request).classify(org, actor, ticket_id)
    except ApplicationClassificationError as exc:
        raise HTTPException(
            status_code=exc.status,
            detail={
                "code": exc.code,
                "message": "Classification request could not be completed",
            },
        ) from None
