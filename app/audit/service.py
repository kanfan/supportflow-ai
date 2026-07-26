from collections.abc import Mapping
from enum import StrEnum
from typing import Final
from uuid import UUID

from sqlalchemy.orm import Session

from app.audit.models import AuditAction, AuditEvent, AuditResourceType
from app.audit.repository import AuditEventPage, AuditEventRepository
from app.identity.models import MembershipRole
from app.tickets.models import TicketSourceType, TicketStatus


TICKET_STATUSES: Final[frozenset[str]] = frozenset(
    status.value for status in TicketStatus
)
TICKET_SOURCE_TYPES: Final[frozenset[str]] = frozenset(
    source_type.value for source_type in TicketSourceType
)
MEMBERSHIP_ROLES: Final[frozenset[str]] = frozenset(
    role.value for role in MembershipRole
)


class AuditMetadataError(ValueError):
    """Raised when audit metadata is outside the action-specific allowlist."""


def enum_value(value: StrEnum | str) -> str:
    return value.value if isinstance(value, StrEnum) else value


class AuditEventService:
    """Build safe audit rows without owning the surrounding transaction."""

    def __init__(self, session: Session, organization_id: UUID) -> None:
        self._organization_id = organization_id
        self._repository = AuditEventRepository(session, organization_id)

    def list_events(self, *, limit: int, offset: int) -> AuditEventPage:
        return self._repository.list_events(limit=limit, offset=offset)

    def record_organization_member_created(
        self,
        *,
        actor_user_id: UUID,
        member_user_id: UUID,
        role: StrEnum | str,
    ) -> AuditEvent:
        role_value = enum_value(role)
        if role_value not in MEMBERSHIP_ROLES:
            raise AuditMetadataError("Unsupported membership role")
        return self._record(
            action=AuditAction.ORGANIZATION_MEMBER_CREATED,
            actor_user_id=actor_user_id,
            resource_type=AuditResourceType.ORGANIZATION_MEMBER,
            resource_id=member_user_id,
            metadata={"role": role_value},
        )

    def record_ticket_created(
        self,
        *,
        actor_user_id: UUID,
        ticket_id: UUID,
        source_type: StrEnum | str,
    ) -> AuditEvent:
        source_value = enum_value(source_type)
        if source_value not in TICKET_SOURCE_TYPES:
            raise AuditMetadataError("Unsupported ticket source type")
        return self._record(
            action=AuditAction.TICKET_CREATED,
            actor_user_id=actor_user_id,
            resource_type=AuditResourceType.TICKET,
            resource_id=ticket_id,
            metadata={"source_type": source_value},
        )

    def record_ticket_message_created(
        self,
        *,
        actor_user_id: UUID | None,
        ticket_message_id: UUID,
    ) -> AuditEvent:
        return self._record(
            action=AuditAction.TICKET_MESSAGE_CREATED,
            actor_user_id=actor_user_id,
            resource_type=AuditResourceType.TICKET_MESSAGE,
            resource_id=ticket_message_id,
            metadata={},
        )

    def record_ticket_status_changed(
        self,
        *,
        actor_user_id: UUID,
        ticket_id: UUID,
        previous_status: StrEnum | str,
        new_status: StrEnum | str,
    ) -> AuditEvent:
        previous_value = enum_value(previous_status)
        new_value = enum_value(new_status)
        if previous_value not in TICKET_STATUSES or new_value not in TICKET_STATUSES:
            raise AuditMetadataError("Unsupported ticket status")
        return self._record(
            action=AuditAction.TICKET_STATUS_CHANGED,
            actor_user_id=actor_user_id,
            resource_type=AuditResourceType.TICKET,
            resource_id=ticket_id,
            metadata={
                "previous_status": previous_value,
                "new_status": new_value,
            },
        )

    def _record(
        self,
        *,
        action: AuditAction,
        actor_user_id: UUID | None,
        resource_type: AuditResourceType,
        resource_id: UUID | None,
        metadata: Mapping[str, str],
    ) -> AuditEvent:
        event = AuditEvent(
            organization_id=self._organization_id,
            actor_user_id=actor_user_id,
            action=action.value,
            resource_type=resource_type.value,
            resource_id=resource_id,
            event_metadata=dict(metadata),
        )
        self._repository.add(event)
        return event
