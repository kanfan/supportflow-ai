from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import (
    OrganizationContext,
    get_database_session,
    require_roles,
)
from app.identity.models import MembershipRole
from app.tickets.models import MessageAuthorType
from app.tickets.schemas import (
    PaginationResponse,
    TicketCreateRequest,
    TicketCreateResponse,
    TicketListResponse,
    TicketMessageResponse,
    TicketResponse,
)
from app.tickets.service import (
    CustomerNotFoundError,
    TicketPersistenceError,
    TicketService,
)


router = APIRouter(prefix="/api/v1/tickets", tags=["tickets"])
ticket_context = require_roles(MembershipRole.ADMIN, MembershipRole.AGENT)


@router.post(
    "", response_model=TicketCreateResponse, status_code=status.HTTP_201_CREATED
)
def create_ticket(
    request: TicketCreateRequest,
    context: Annotated[OrganizationContext, Depends(ticket_context)],
    session: Annotated[Session, Depends(get_database_session)],
) -> TicketCreateResponse:
    service = TicketService(session, context.organization.id)
    try:
        result = service.create_ticket(
            subject=request.subject,
            source_type=request.source_type,
            external_id=request.external_id,
            customer_id=request.customer_id,
            initial_message_body=request.initial_message.body,
            initial_message_author_type=MessageAuthorType(
                request.initial_message.author_type
            ),
            current_user_id=context.membership.user_id,
        )
    except CustomerNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found",
        ) from None
    except TicketPersistenceError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ticket could not be created",
        ) from None

    ticket_response = TicketResponse.model_validate(result.ticket)
    return TicketCreateResponse(
        **ticket_response.model_dump(),
        initial_message=TicketMessageResponse.model_validate(result.initial_message),
    )


@router.get("", response_model=TicketListResponse)
def list_tickets(
    context: Annotated[OrganizationContext, Depends(ticket_context)],
    session: Annotated[Session, Depends(get_database_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TicketListResponse:
    page = TicketService(session, context.organization.id).list_tickets(
        limit=limit,
        offset=offset,
    )
    return TicketListResponse(
        items=[TicketResponse.model_validate(ticket) for ticket in page.items],
        pagination=PaginationResponse(
            limit=limit,
            offset=offset,
            total=page.total,
        ),
    )
