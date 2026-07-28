from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.tickets.models import (
    Customer,
    Ticket,
    TicketMessage,
    TicketSourceType,
    TicketStatus,
)


@dataclass(frozen=True)
class TicketPage:
    items: list[Ticket]
    total: int


class TicketRepository:
    """Organization-scoped persistence operations for tickets and customers."""

    def __init__(self, session: Session, organization_id: UUID) -> None:
        self._session = session
        self._organization_id = organization_id

    def get_customer(self, customer_id: UUID) -> Customer | None:
        return self._session.scalar(
            select(Customer).where(
                Customer.organization_id == self._organization_id,
                Customer.id == customer_id,
            )
        )

    def get_ticket(self, ticket_id: UUID) -> Ticket | None:
        return self._session.scalar(
            select(Ticket).where(
                Ticket.organization_id == self._organization_id,
                Ticket.id == ticket_id,
            )
        )

    def list_ticket_messages(self, ticket_id: UUID) -> list[TicketMessage]:
        return list(
            self._session.scalars(
                select(TicketMessage)
                .where(
                    TicketMessage.organization_id == self._organization_id,
                    TicketMessage.ticket_id == ticket_id,
                )
                .order_by(TicketMessage.created_at.asc(), TicketMessage.id.asc())
            )
        )

    def list_tickets(
        self,
        *,
        limit: int,
        offset: int,
        ticket_status: TicketStatus | None = None,
        source_type: TicketSourceType | None = None,
        customer_id: UUID | None = None,
    ) -> TicketPage:
        filters = [Ticket.organization_id == self._organization_id]
        if ticket_status is not None:
            filters.append(Ticket.status == ticket_status)
        if source_type is not None:
            filters.append(Ticket.source_type == source_type)
        if customer_id is not None:
            filters.append(Ticket.customer_id == customer_id)

        items = list(
            self._session.scalars(
                select(Ticket)
                .where(*filters)
                .order_by(Ticket.created_at.desc(), Ticket.id.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        total = self._session.scalar(
            select(func.count()).select_from(Ticket).where(*filters)
        )
        return TicketPage(items=items, total=total or 0)
