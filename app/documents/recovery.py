"""Audited, single-intent operator rearm; no retry-all or counter reset."""

from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.audit.service import AuditEventService
from app.documents.models import (
    DocumentIngestionIntent as Intent,
    DocumentVersion as Version,
    DocumentProcessingStatus as Status,
)
from app.identity.models import Organization, OrganizationMember, User


def rearm_intent(
    sessions: Callable[[], Session],
    *,
    organization_id: UUID,
    intent_id: UUID,
    actor_user_id: UUID,
    verify_dependencies: Callable[[], object],
    extracting_seconds: int,
    max_worker_attempts: int,
) -> int:
    # External health check BEFORE row/advisory locks, and explicit operational
    # repair confirmation in the CLI. This is a trusted DB-operator command,
    # not a public impersonation endpoint; actor must be a tenant's active admin.
    if not verify_dependencies():
        raise ValueError("Dependency verification failed")
    with sessions() as session, session.begin():
        session.execute(text("SET LOCAL statement_timeout = '2000ms'"))
        actor = session.scalar(
            select(OrganizationMember)
            .join(User)
            .join(Organization)
            .where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.user_id == actor_user_id,
                OrganizationMember.role == "admin",
                OrganizationMember.status == "active",
                User.status == "active",
                Organization.status == "active",
            )
            .with_for_update()
        )
        if actor is None:
            raise ValueError("Active tenant admin required")
        intent = session.scalar(
            select(Intent)
            .where(Intent.id == intent_id, Intent.organization_id == organization_id)
            .with_for_update()
        )
        if (
            intent is None
            or intent.unresolved_at is None
            or intent.last_error != "attempts_exhausted"
            or intent.settled_at is not None
        ):
            raise ValueError("Only exhausted unresolved intent can be rearmed")
        now = session.scalar(select(func.now()))
        assert isinstance(now, datetime)
        if intent.lease_expires_at is not None and intent.lease_expires_at > now:
            raise ValueError("Active relay lease")
        if intent.recovery_grants >= 3:
            raise ValueError("Operator recovery grant limit reached")
        # Same key as the worker's session lock; transaction-scoped here, so it
        # cannot survive an exception, rollback or return to the connection pool.
        if not session.scalar(
            text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": str(intent.document_version_id)},
        ):
            raise ValueError("Active worker delivery")
        version = session.scalar(
            select(Version)
            .where(
                Version.id == intent.document_version_id,
                Version.organization_id == organization_id,
            )
            .with_for_update()
        )
        assert version
        if version.status in (Status.READY, Status.FAILED):
            raise ValueError("Terminal version needs settlement, not replay")
        if version.processing_task_id not in (None, intent.task_id):
            raise ValueError("Ambiguous worker ownership")
        if version.status == Status.EXTRACTING and (
            version.processing_task_id != intent.task_id
            or version.processing_started_at is None
            or version.processing_started_at + timedelta(seconds=extracting_seconds)
            > now
        ):
            raise ValueError("Extraction ownership is active or ambiguous")
        if (
            version.status == Status.QUEUED
            and version.attempt_count >= max_worker_attempts
        ):
            raise ValueError("Worker attempt budget exhausted")
        intent.recovery_grants += (
            1  # one extra publication/recovery, never reset counters
        )
        intent.unresolved_at = None
        intent.last_error = None
        intent.lease_token = None
        intent.lease_expires_at = None
        intent.available_at = now
        AuditEventService(session, organization_id).record_document_dispatch_rearmed(
            actor_user_id=actor_user_id,
            version_id=version.id,
            grant_number=intent.recovery_grants,
        )
        session.flush()
        return intent.recovery_grants
