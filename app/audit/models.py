from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.identity.models import UUIDPrimaryKeyMixin
from app.infrastructure.database import Base


class AuditAction(StrEnum):
    ORGANIZATION_MEMBER_CREATED = "organization_member.created"
    TICKET_CREATED = "ticket.created"
    TICKET_MESSAGE_CREATED = "ticket_message.created"
    TICKET_STATUS_CHANGED = "ticket.status_changed"
    DOCUMENT_UPLOADED = "document.uploaded"
    DOCUMENT_READY = "document.ready"
    DOCUMENT_FAILED = "document.failed"
    DOCUMENT_DISPATCH_REARMED = "document.dispatch_rearmed"


class AuditResourceType(StrEnum):
    ORGANIZATION_MEMBER = "organization_member"
    TICKET = "ticket"
    TICKET_MESSAGE = "ticket_message"
    DOCUMENT = "document"
    DOCUMENT_VERSION = "document_version"


class AuditEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint(
            "action = lower(action) "
            r"AND action ~ '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$'",
            name="action_lowercase_dotted",
        ),
        CheckConstraint(
            "resource_type = lower(resource_type) "
            "AND resource_type ~ '^[a-z][a-z0-9_]*$'",
            name="resource_type_lowercase",
        ),
        CheckConstraint(
            "jsonb_typeof(metadata) = 'object'",
            name="metadata_object",
        ),
        ForeignKeyConstraint(
            ["organization_id", "actor_user_id"],
            [
                "organization_members.organization_id",
                "organization_members.user_id",
            ],
            name="fk_audit_events_organization_id_organization_members",
        ),
        Index(
            "ix_audit_events_organization_created_id",
            "organization_id",
            "created_at",
            "id",
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"),
        nullable=False,
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(nullable=True)
    action: Mapped[str] = mapped_column(String(100))
    resource_type: Mapped[str] = mapped_column(String(100))
    resource_id: Mapped[UUID | None] = mapped_column(nullable=True)
    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
