"""Tenant-scoped fake application claims; no external-provider enablement."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.identity.models import UUIDPrimaryKeyMixin
from app.infrastructure.database import Base


class ClassificationOperation(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "classification_operations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "ticket_id"],
            ["tickets.organization_id", "tickets.id"],
            name="fk_classification_ticket",
        ),
        ForeignKeyConstraint(
            ["organization_id", "requesting_actor_user_id"],
            ["organization_members.organization_id", "organization_members.user_id"],
            name="fk_classification_actor_membership",
        ),
        UniqueConstraint(
            "organization_id",
            "ticket_id",
            "input_fingerprint",
            "config_digest",
            name="uq_classification_identity",
        ),
        CheckConstraint(
            "state IN ('in_progress','succeeded','stale','failed','unknown')",
            name="state",
        ),
        CheckConstraint("mode = 'fake'", name="fake_only"),
        CheckConstraint("attempt_count IN (0,1)", name="attempt_count"),
        CheckConstraint("elapsed_ms IS NULL OR elapsed_ms >= 0", name="elapsed_ms"),
        CheckConstraint(
            "input_tokens IS NULL AND output_tokens IS NULL AND cost IS NULL",
            name="fake_usage_unmeasured",
        ),
        CheckConstraint(
            "(state = 'succeeded' AND error_code IS NULL AND completed_at IS NOT NULL "
            "AND ((outcome = 'classified' AND category IS NOT NULL AND category IN "
            "('account_access','billing','technical_issue','how_to','feature_request','other')) "
            "OR (outcome = 'insufficient_context' AND category IS NULL))) "
            "OR (state <> 'succeeded' AND outcome IS NULL AND category IS NULL)",
            name="result_pair",
        ),
        CheckConstraint(
            "state <> 'succeeded' OR outcome IS NOT NULL", name="success_outcome"
        ),
        Index(
            "ix_classification_ticket_created",
            "organization_id",
            "ticket_id",
            "created_at",
            "id",
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(nullable=False)
    ticket_id: Mapped[UUID] = mapped_column(nullable=False)
    requesting_actor_user_id: Mapped[UUID] = mapped_column(nullable=False)
    opening_message_id: Mapped[UUID | None] = mapped_column(nullable=True)
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    config_digest: Mapped[str] = mapped_column(String(64))
    configuration_version: Mapped[str] = mapped_column(String(100))
    input_policy_version: Mapped[str] = mapped_column(String(100))
    taxonomy_version: Mapped[str] = mapped_column(String(100))
    schema_version: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(100))
    adapter_version: Mapped[str] = mapped_column(String(100))
    provider: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(100))
    mode: Mapped[str] = mapped_column(String(10))
    state: Mapped[str] = mapped_column(String(20))
    outcome: Mapped[str | None] = mapped_column(String(30))
    category: Mapped[str | None] = mapped_column(String(30))
    error_code: Mapped[str | None] = mapped_column(String(60))
    attempt_count: Mapped[int] = mapped_column(Integer)
    elapsed_ms: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
