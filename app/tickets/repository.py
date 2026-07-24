from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.tickets.models import Customer, Ticket


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

    def list_tickets(self, *, limit: int, offset: int) -> TicketPage:
        items = list(
            self._session.scalars(
                select(Ticket)
                .where(Ticket.organization_id == self._organization_id)
                .order_by(Ticket.created_at.desc(), Ticket.id.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        total = self._session.scalar(
            select(func.count())
            .select_from(Ticket)
            .where(Ticket.organization_id == self._organization_id)
        )
        return TicketPage(items=items, total=total or 0)
