from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.tickets.models import (
    MessageAuthorType,
    Ticket,
    TicketMessage,
    TicketSourceType,
    TicketStatus,
)
from app.tickets.repository import TicketPage, TicketRepository


class CustomerNotFoundError(LookupError):
    """Raised when a customer is absent from the selected organization."""


class TicketNotFoundError(LookupError):
    """Raised when a ticket is absent from the selected organization."""


class InvalidTicketTransitionError(ValueError):
    """Raised when a status transition is outside the agreed Week 2 graph."""


class TicketPersistenceError(ValueError):
    """Raised when database constraints reject a ticket operation."""


@dataclass(frozen=True)
class CreatedTicket:
    ticket: Ticket
    initial_message: TicketMessage


ALLOWED_TRANSITIONS = {
    TicketStatus.OPEN: TicketStatus.PROCESSING,
    TicketStatus.PROCESSING: TicketStatus.WAITING_FOR_AGENT,
    TicketStatus.WAITING_FOR_AGENT: TicketStatus.RESOLVED,
    TicketStatus.RESOLVED: TicketStatus.CLOSED,
}


class TicketService:
    def __init__(self, session: Session, organization_id: UUID) -> None:
        self._session = session
        self._organization_id = organization_id
        self._repository = TicketRepository(session, organization_id)

    def create_ticket(
        self,
        *,
        subject: str,
        source_type: TicketSourceType,
        external_id: str | None,
        customer_id: UUID | None,
        initial_message_body: str,
        initial_message_author_type: MessageAuthorType,
        current_user_id: UUID,
    ) -> CreatedTicket:
        if (
            customer_id is not None
            and self._repository.get_customer(customer_id) is None
        ):
            raise CustomerNotFoundError
        if (
            initial_message_author_type is MessageAuthorType.CUSTOMER
            and customer_id is None
        ):
            raise CustomerNotFoundError
        if initial_message_author_type is MessageAuthorType.SYSTEM:
            raise ValueError("Public ticket creation cannot impersonate the system")

        ticket_id = uuid4()
        ticket = Ticket(
            id=ticket_id,
            organization_id=self._organization_id,
            customer_id=customer_id,
            source_type=source_type,
            external_id=external_id,
            subject=subject,
            status=TicketStatus.OPEN,
        )
        message = TicketMessage(
            id=uuid4(),
            organization_id=self._organization_id,
            ticket_id=ticket_id,
            author_type=initial_message_author_type,
            author_user_id=(
                current_user_id
                if initial_message_author_type is MessageAuthorType.AGENT
                else None
            ),
            author_customer_id=(
                customer_id
                if initial_message_author_type is MessageAuthorType.CUSTOMER
                else None
            ),
            body=initial_message_body,
        )
        self._session.add_all([ticket, message])

        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise TicketPersistenceError from exc

        return CreatedTicket(ticket=ticket, initial_message=message)

    def list_tickets(self, *, limit: int, offset: int) -> TicketPage:
        return self._repository.list_tickets(limit=limit, offset=offset)

    def transition_ticket(
        self,
        *,
        ticket_id: UUID,
        next_status: TicketStatus,
    ) -> Ticket:
        ticket = self._repository.get_ticket(ticket_id)
        if ticket is None:
            raise TicketNotFoundError

        if ALLOWED_TRANSITIONS.get(ticket.status) is not next_status:
            raise InvalidTicketTransitionError(
                f"Cannot transition ticket from {ticket.status} to {next_status}"
            )

        ticket.status = next_status
        self._session.commit()
        return ticket
