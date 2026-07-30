from collections.abc import Mapping
from enum import StrEnum
from typing import Final
from uuid import UUID

from sqlalchemy.orm import Session

from app.audit.models import AuditAction, AuditEvent, AuditResourceType
from app.audit.repository import AuditEventPage, AuditEventRepository
from app.documents.models import DocumentErrorCode, DocumentMediaType
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
DOCUMENT_MEDIA_TYPES: Final[frozenset[str]] = frozenset(
    media_type.value for media_type in DocumentMediaType
)
DOCUMENT_ERROR_CODES: Final[frozenset[str]] = frozenset(
    error_code.value for error_code in DocumentErrorCode
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

    def record_document_uploaded(
        self,
        *,
        actor_user_id: UUID,
        document_id: UUID,
        version_number: int,
        media_type: StrEnum | str,
        size_bytes: int,
    ) -> AuditEvent:
        media_type_value = enum_value(media_type)
        if (
            media_type_value not in DOCUMENT_MEDIA_TYPES
            or version_number < 1
            or size_bytes < 1
        ):
            raise AuditMetadataError("Unsupported document upload metadata")
        return self._record(
            action=AuditAction.DOCUMENT_UPLOADED,
            actor_user_id=actor_user_id,
            resource_type=AuditResourceType.DOCUMENT,
            resource_id=document_id,
            metadata={
                "version_number": version_number,
                "media_type": media_type_value,
                "size_bytes": size_bytes,
            },
        )

    def record_document_ready(
        self,
        *,
        document_version_id: UUID,
        version_number: int,
        attempt_count: int,
        extracted_character_count: int,
    ) -> AuditEvent:
        if version_number < 1 or attempt_count < 1 or extracted_character_count < 0:
            raise AuditMetadataError("Unsupported document ready metadata")
        return self._record(
            action=AuditAction.DOCUMENT_READY,
            actor_user_id=None,
            resource_type=AuditResourceType.DOCUMENT_VERSION,
            resource_id=document_version_id,
            metadata={
                "version_number": version_number,
                "attempt_count": attempt_count,
                "extracted_character_count": extracted_character_count,
            },
        )

    def record_document_failed(
        self,
        *,
        document_version_id: UUID,
        version_number: int,
        error_code: StrEnum | str,
        attempt_count: int,
    ) -> AuditEvent:
        error_code_value = enum_value(error_code)
        if (
            error_code_value not in DOCUMENT_ERROR_CODES
            or version_number < 1
            or attempt_count < 0
        ):
            raise AuditMetadataError("Unsupported document failure metadata")
        return self._record(
            action=AuditAction.DOCUMENT_FAILED,
            actor_user_id=None,
            resource_type=AuditResourceType.DOCUMENT_VERSION,
            resource_id=document_version_id,
            metadata={
                "version_number": version_number,
                "error_code": error_code_value,
                "attempt_count": attempt_count,
            },
        )

    def _record(
        self,
        *,
        action: AuditAction,
        actor_user_id: UUID | None,
        resource_type: AuditResourceType,
        resource_id: UUID | None,
        metadata: Mapping[str, str | int],
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
