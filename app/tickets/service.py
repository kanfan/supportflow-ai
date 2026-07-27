from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import AuditEventService
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

    def __init__(
        self,
        *,
        current_status: TicketStatus,
        requested_status: TicketStatus,
    ) -> None:
        self.current_status = current_status
        self.requested_status = requested_status
        super().__init__(
            f"Cannot transition ticket from {current_status} to {requested_status}"
        )


class TicketPersistenceError(ValueError):
    """Raised when database constraints reject a ticket operation."""


@dataclass(frozen=True)
class CreatedTicket:
    ticket: Ticket
    initial_message: TicketMessage


@dataclass(frozen=True)
class TicketDetail:
    ticket: Ticket
    messages: list[TicketMessage]


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
        self._audit = AuditEventService(session, organization_id)

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
        if initial_message_author_type is not MessageAuthorType.AGENT:
            raise ValueError("Public ticket creation requires an agent author")

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
            author_user_id=current_user_id,
            author_customer_id=None,
            body=initial_message_body,
        )
        self._session.add_all([ticket, message])

        try:
            self._audit.record_ticket_created(
                actor_user_id=current_user_id,
                ticket_id=ticket.id,
                source_type=ticket.source_type,
            )
            self._audit.record_ticket_message_created(
                actor_user_id=current_user_id,
                ticket_message_id=message.id,
            )
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise TicketPersistenceError from exc
        except Exception:
            self._session.rollback()
            raise

        return CreatedTicket(ticket=ticket, initial_message=message)

    def list_tickets(
        self,
        *,
        limit: int,
        offset: int,
        ticket_status: TicketStatus | None = None,
        source_type: TicketSourceType | None = None,
        customer_id: UUID | None = None,
    ) -> TicketPage:
        return self._repository.list_tickets(
            limit=limit,
            offset=offset,
            ticket_status=ticket_status,
            source_type=source_type,
            customer_id=customer_id,
        )

    def get_ticket_detail(self, ticket_id: UUID) -> TicketDetail:
        ticket = self._repository.get_ticket(ticket_id)
        if ticket is None:
            raise TicketNotFoundError
        return TicketDetail(
            ticket=ticket,
            messages=self._repository.list_ticket_messages(ticket_id),
        )

    def append_agent_message(
        self,
        *,
        ticket_id: UUID,
        body: str,
        current_user_id: UUID,
    ) -> TicketMessage:
        if self._repository.get_ticket(ticket_id) is None:
            raise TicketNotFoundError

        message = TicketMessage(
            id=uuid4(),
            organization_id=self._organization_id,
            ticket_id=ticket_id,
            author_type=MessageAuthorType.AGENT,
            author_user_id=current_user_id,
            author_customer_id=None,
            body=body,
        )
        self._session.add(message)
        try:
            self._audit.record_ticket_message_created(
                actor_user_id=current_user_id,
                ticket_message_id=message.id,
            )
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise TicketPersistenceError from exc
        except Exception:
            self._session.rollback()
            raise
        return message

    def transition_ticket(
        self,
        *,
        ticket_id: UUID,
        next_status: TicketStatus,
        current_user_id: UUID,
    ) -> Ticket:
        ticket = self._repository.get_ticket(ticket_id)
        if ticket is None:
            raise TicketNotFoundError

        if ALLOWED_TRANSITIONS.get(ticket.status) is not next_status:
            raise InvalidTicketTransitionError(
                current_status=ticket.status,
                requested_status=next_status,
            )

        previous_status = ticket.status
        ticket.status = next_status
        try:
            self._audit.record_ticket_status_changed(
                actor_user_id=current_user_id,
                ticket_id=ticket.id,
                previous_status=previous_status,
                new_status=next_status,
            )
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise TicketPersistenceError from exc
        except Exception:
            self._session.rollback()
            raise
        return ticket
