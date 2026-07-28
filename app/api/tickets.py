from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import (
    OrganizationContext,
    get_database_session,
    require_roles,
)
from app.identity.models import MembershipRole
from app.tickets.models import (
    MessageAuthorType,
    TicketSourceType,
    TicketStatus,
)
from app.tickets.schemas import (
    PaginationResponse,
    TicketCreateRequest,
    TicketCreateResponse,
    TicketDetailResponse,
    TicketListResponse,
    TicketMessageCreateRequest,
    TicketMessageResponse,
    TicketResponse,
    TicketStatusUpdateRequest,
)
from app.tickets.service import (
    CustomerNotFoundError,
    InvalidTicketTransitionError,
    TicketNotFoundError,
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
    ticket_status: Annotated[
        TicketStatus | None,
        Query(alias="status"),
    ] = None,
    source_type: TicketSourceType | None = None,
    customer_id: UUID | None = None,
) -> TicketListResponse:
    page = TicketService(session, context.organization.id).list_tickets(
        limit=limit,
        offset=offset,
        ticket_status=ticket_status,
        source_type=source_type,
        customer_id=customer_id,
    )
    return TicketListResponse(
        items=[TicketResponse.model_validate(ticket) for ticket in page.items],
        pagination=PaginationResponse(
            limit=limit,
            offset=offset,
            total=page.total,
        ),
    )


@router.get("/{ticket_id}", response_model=TicketDetailResponse)
def get_ticket_detail(
    ticket_id: UUID,
    context: Annotated[OrganizationContext, Depends(ticket_context)],
    session: Annotated[Session, Depends(get_database_session)],
) -> TicketDetailResponse:
    try:
        detail = TicketService(
            session,
            context.organization.id,
        ).get_ticket_detail(ticket_id)
    except TicketNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ticket not found",
        ) from None

    ticket_response = TicketResponse.model_validate(detail.ticket)
    return TicketDetailResponse(
        **ticket_response.model_dump(),
        messages=[
            TicketMessageResponse.model_validate(message) for message in detail.messages
        ],
    )


@router.post(
    "/{ticket_id}/messages",
    response_model=TicketMessageResponse,
    status_code=status.HTTP_201_CREATED,
)
def append_ticket_message(
    ticket_id: UUID,
    request: TicketMessageCreateRequest,
    context: Annotated[OrganizationContext, Depends(ticket_context)],
    session: Annotated[Session, Depends(get_database_session)],
) -> TicketMessageResponse:
    try:
        message = TicketService(
            session,
            context.organization.id,
        ).append_agent_message(
            ticket_id=ticket_id,
            body=request.body,
            current_user_id=context.membership.user_id,
        )
    except TicketNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ticket not found",
        ) from None
    except TicketPersistenceError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ticket message could not be created",
        ) from None
    return TicketMessageResponse.model_validate(message)


@router.patch("/{ticket_id}/status", response_model=TicketResponse)
def update_ticket_status(
    ticket_id: UUID,
    request: TicketStatusUpdateRequest,
    context: Annotated[OrganizationContext, Depends(ticket_context)],
    session: Annotated[Session, Depends(get_database_session)],
) -> TicketResponse:
    try:
        ticket = TicketService(
            session,
            context.organization.id,
        ).transition_ticket(
            ticket_id=ticket_id,
            next_status=request.status,
            current_user_id=context.membership.user_id,
        )
    except TicketNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ticket not found",
        ) from None
    except InvalidTicketTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "invalid_ticket_transition",
                "message": "The requested ticket status transition is not allowed",
                "details": {
                    "current_status": exc.current_status.value,
                    "requested_status": exc.requested_status.value,
                },
            },
        ) from None
    except TicketPersistenceError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ticket status could not be updated",
        ) from None
    return TicketResponse.model_validate(ticket)
