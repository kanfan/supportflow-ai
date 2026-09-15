"""Bounded at-least-once publication. No broker I/O inside DB transactions."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.documents.models import (
    DocumentIngestionIntent as Intent,
    DocumentVersion as Version,
    DocumentProcessingStatus as Status,
)


@dataclass(frozen=True)
class RelayPolicy:
    lease_seconds: int = 30
    recovery_seconds: int = 1200
    extracting_seconds: int = 375
    max_publications: int = 20
    max_recoveries: int = 3


@dataclass(frozen=True)
class Publication:
    intent_id: UUID
    version_id: UUID
    task_id: str
    token: UUID
    retries: int


class IngestionRelay:
    def __init__(
        self,
        sessions: Callable[[], Session],
        publish: Callable[[Publication], None],
        policy: RelayPolicy = RelayPolicy(),
    ) -> None:
        self.sessions = sessions
        self.publish = publish
        self.policy = policy

    def claim(self) -> Publication | None:
        with self.sessions() as session, session.begin():
            session.execute(text("SET LOCAL statement_timeout = '2000ms'"))
            now = session.scalar(select(func.now()))
            assert isinstance(now, datetime)
            intent = session.scalar(
                select(Intent)
                .where(
                    Intent.settled_at.is_(None),
                    Intent.unresolved_at.is_(None),
                    Intent.available_at <= now,
                    or_(
                        Intent.lease_expires_at.is_(None),
                        Intent.lease_expires_at <= now,
                    ),
                )
                .order_by(Intent.available_at, Intent.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if intent is None:
                return None
            version = session.scalar(
                select(Version)
                .where(
                    Version.id == intent.document_version_id,
                    Version.organization_id == intent.organization_id,
                )
                .with_for_update()
            )
            assert version is not None  # composite FK; no cascade deletion
            intent.lease_token = None
            intent.lease_expires_at = None
            if version.status in (Status.READY, Status.FAILED):
                intent.settled_at = now
                return None
            if version.status == Status.EXTRACTING:
                if (
                    version.processing_task_id != intent.task_id
                    or version.processing_started_at is None
                ):
                    intent.unresolved_at, intent.last_error = now, "ownership_ambiguous"
                    return None
                safe_after = version.processing_started_at + timedelta(
                    seconds=self.policy.extracting_seconds
                )
                if now < safe_after:
                    intent.available_at = safe_after
                    return None
            elif version.processing_task_id not in (None, intent.task_id):
                intent.unresolved_at, intent.last_error = now, "ownership_ambiguous"
                return None
            if (
                intent.publish_attempts >= self.policy.max_publications
                or intent.recovery_attempts >= self.policy.max_recoveries
            ):
                intent.unresolved_at, intent.last_error = now, "attempts_exhausted"
                return None
            if intent.published_at is not None:
                intent.recovery_attempts += 1
            intent.publish_attempts += 1
            token = uuid4()
            expires_at = now + timedelta(seconds=self.policy.lease_seconds)
            intent.lease_token = token
            intent.lease_expires_at = expires_at
            intent.available_at = expires_at
            # A recovered queued retry must not reset the Celery retry budget.
            retries = max(
                0,
                version.attempt_count
                - (1 if version.status == Status.EXTRACTING else 0),
            )
            return Publication(intent.id, version.id, intent.task_id, token, retries)

    def finish(self, publication: Publication, *, success: bool) -> bool:
        with self.sessions() as session, session.begin():
            session.execute(text("SET LOCAL statement_timeout = '2000ms'"))
            now = session.scalar(select(func.now()))
            assert isinstance(now, datetime)
            intent = session.scalar(
                select(Intent)
                .where(
                    Intent.id == publication.intent_id,
                    Intent.lease_token == publication.token,
                    Intent.lease_expires_at > now,
                )
                .with_for_update()
            )
            if intent is None:
                return False
            intent.lease_token = None
            intent.lease_expires_at = None
            intent.last_error = None if success else "publish_unavailable"
            if success:
                intent.published_at = now
            delay = (
                self.policy.recovery_seconds
                if success
                else min(60, 2 ** min(intent.publish_attempts, 6))
            )
            intent.available_at = now + timedelta(seconds=delay)
            return True

    def tick(self) -> bool:
        publication = self.claim()
        if publication is None:
            return False
        try:
            self.publish(publication)
        except Exception:
            self.finish(publication, success=False)
        else:
            self.finish(publication, success=True)
        return True

    def cleanup(self, *, limit: int = 100) -> int:
        if not 1 <= limit <= 1000:
            raise ValueError("Bounded cleanup batch required")
        with self.sessions() as session, session.begin():
            session.execute(text("SET LOCAL statement_timeout = '2000ms'"))
            rows = list(
                session.scalars(
                    select(Intent.id)
                    .where(
                        Intent.settled_at <= func.now() - timedelta(days=7),
                        Intent.unresolved_at.is_(None),
                        Intent.lease_token.is_(None),
                    )
                    .order_by(Intent.settled_at, Intent.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            if rows:
                session.execute(delete(Intent).where(Intent.id.in_(rows)))
            return len(rows)


def backfill(
    sessions: Callable[[], Session], *, cutoff: datetime, limit: int = 100
) -> int:
    """Caller pauses old producers/workers; retry with the SAME aware cutoff."""
    if cutoff.tzinfo is None or not 1 <= limit <= 1000:
        raise ValueError("Aware cutoff and bounded batch required")
    with sessions() as session, session.begin():
        session.execute(text("SET LOCAL statement_timeout = '2000ms'"))
        now = session.scalar(select(func.now()))
        assert isinstance(now, datetime)
        if cutoff > now:
            raise ValueError("Cutoff cannot be in the future")
        versions = list(
            session.scalars(
                select(Version)
                .where(
                    Version.created_at <= cutoff,
                    Version.status == Status.QUEUED,
                    ~select(Intent.id)
                    .where(
                        Intent.document_version_id == Version.id,
                        Intent.organization_id == Version.organization_id,
                    )
                    .exists(),
                )
                .order_by(Version.created_at, Version.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        for version in versions:
            session.execute(
                insert(Intent)
                .values(
                    id=uuid4(),
                    organization_id=version.organization_id,
                    document_version_id=version.id,
                    task_id=version.processing_task_id or str(uuid4()),
                )
                .on_conflict_do_nothing(
                    index_elements=[Intent.organization_id, Intent.document_version_id]
                )
            )
        return len(versions)
