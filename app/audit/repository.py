from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent


@dataclass(frozen=True)
class AuditEventPage:
    items: list[AuditEvent]
    total: int


class AuditEventRepository:
    """Append-only persistence operations scoped to one organization."""

    def __init__(self, session: Session, organization_id: UUID) -> None:
        self._session = session
        self._organization_id = organization_id

    def add(self, event: AuditEvent) -> None:
        if event.organization_id != self._organization_id:
            raise ValueError("Audit event organization does not match repository")
        self._session.add(event)

    def list_events(self, *, limit: int, offset: int) -> AuditEventPage:
        items = list(
            self._session.scalars(
                select(AuditEvent)
                .where(AuditEvent.organization_id == self._organization_id)
                .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        total = self._session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.organization_id == self._organization_id)
        )
        return AuditEventPage(items=items, total=total or 0)
