"""Synchronous fake-only integration. Session ownership stops at each I/O boundary."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from time import monotonic
from typing import Literal, cast
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.audit.service import AuditEventService
from app.classification.errors import (
    InputTooLargeError,
    InvalidProviderOutputError,
    ProviderAuthenticationError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.classification.inputs import (
    INPUT_POLICY_VERSION,
    TAXONOMY_VERSION,
    OpeningMessage,
    PreparedInput,
    prepare_input,
)
from app.classification.models import ClassificationOperation
from app.classification.providers import FakeClassificationProvider
from app.classification.schemas import (
    SCHEMA_VERSION,
    ClassificationResult,
    parse_provider_output,
)
from app.identity.models import (
    MembershipRole,
    MembershipStatus,
    Organization,
    OrganizationMember,
    OrganizationStatus,
    User,
    UserStatus,
)
from app.tickets.models import Ticket
from app.tickets.repository import TicketRepository


class ApplicationClassificationError(RuntimeError):
    def __init__(self, code: str, status: int) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


class ClassificationView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    state: Literal[
        "not_requested", "current", "stale", "in_progress", "failed", "unknown"
    ]
    mode: Literal["fake"] = "fake"
    result_id: UUID | None = None
    completed_at: datetime | None = None
    configuration_version: str
    result: ClassificationResult | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class FakeClassificationRuntime:
    provider: FakeClassificationProvider
    configuration_version: str = "fake_application.v1"

    def __post_init__(self) -> None:
        if not isinstance(self.provider, FakeClassificationProvider):
            raise ValueError("Only an explicitly scripted fake is supported")
        if not 1 <= len(self.configuration_version) <= 100:
            raise ValueError("Invalid classification configuration version")

    @property
    def digest(self) -> str:
        value = [
            self.configuration_version,
            self.provider.script_fingerprint,
            INPUT_POLICY_VERSION,
            TAXONOMY_VERSION,
            SCHEMA_VERSION,
            "scripted.v1",
            "fake.v1",
            "fake",
            "scripted-no-model",
        ]
        return sha256(json.dumps(value).encode()).hexdigest()


def _authorized(
    session: Session, organization_id: UUID, actor_id: UUID, *, lock: bool
) -> bool:
    query = (
        select(OrganizationMember)
        .join(Organization, Organization.id == OrganizationMember.organization_id)
        .join(User, User.id == OrganizationMember.user_id)
        .where(
            OrganizationMember.organization_id == organization_id,
            OrganizationMember.user_id == actor_id,
            OrganizationMember.status == MembershipStatus.ACTIVE,
            OrganizationMember.role.in_([MembershipRole.ADMIN, MembershipRole.AGENT]),
            Organization.status == OrganizationStatus.ACTIVE,
            User.status == UserStatus.ACTIVE,
        )
    )
    if lock:
        query = query.with_for_update(read=True)
    return session.scalar(query) is not None


def _input(session: Session, ticket: Ticket) -> PreparedInput:
    messages = TicketRepository(session, ticket.organization_id).list_ticket_messages(
        ticket.id
    )
    return prepare_input(
        ticket.subject,
        [
            OpeningMessage(
                m.id,
                m.created_at,
                cast(Literal["agent", "customer", "system"], m.author_type.value),
                m.body,
            )
            for m in messages
        ],
    )


ERROR_STATUS = {
    "provider_timeout": 504,
    "provider_unavailable": 503,
    "provider_authentication": 503,
    "provider_rate_limited": 503,
    "invalid_provider_output": 502,
    "input_too_large": 422,
    "authorization_changed": 403,
    "input_changed": 409,
    "classification_unknown": 409,
}


class ClassificationApplication:
    def __init__(
        self, sessions: sessionmaker[Session], runtime: FakeClassificationRuntime | None
    ) -> None:
        self.sessions = sessions
        self.runtime = runtime

    def _ticket(
        self, session: Session, org: UUID, actor: UUID, ticket_id: UUID, *, lock: bool
    ) -> Ticket:
        if not _authorized(session, org, actor, lock=lock):
            raise ApplicationClassificationError("classification_forbidden", 403)
        query = select(Ticket).where(
            Ticket.organization_id == org, Ticket.id == ticket_id
        )
        if lock:
            query = query.with_for_update()
        ticket = session.scalar(query)
        if ticket is None:
            raise ApplicationClassificationError("ticket_not_found", 404)
        return ticket

    def _view(
        self, operation: ClassificationOperation | None, *, matches: bool = True
    ) -> ClassificationView:
        version = self.runtime.configuration_version if self.runtime else "disabled"
        if operation is None:
            return ClassificationView(
                state="not_requested", configuration_version=version
            )
        state = operation.state
        error = operation.error_code
        if state == "in_progress" and operation.expires_at <= datetime.now(UTC):
            state, error = "unknown", "classification_unknown"
        if not matches:
            state = "stale"
        if state == "succeeded":
            state = "current"
        result = (
            ClassificationResult.model_validate(
                {"outcome": operation.outcome, "category": operation.category}
            )
            if state == "current"
            else None
        )
        return ClassificationView.model_validate(
            dict(
                state=state,
                result_id=operation.id,
                completed_at=operation.completed_at,
                configuration_version=operation.configuration_version,
                result=result,
                error_code=error,
            )
        )

    def read(self, org: UUID, actor: UUID, ticket_id: UUID) -> ClassificationView:
        with self.sessions.begin() as session:
            # Same ticket lock as writers gives a consistent input/result snapshot.
            ticket = self._ticket(session, org, actor, ticket_id, lock=True)
            operations = select(ClassificationOperation).where(
                ClassificationOperation.organization_id == org,
                ClassificationOperation.ticket_id == ticket_id,
            )
            try:
                prepared = _input(session, ticket)
            except InputTooLargeError:
                prepared = None
            match = None
            if prepared is not None and self.runtime is not None:
                match = session.scalar(
                    operations.where(
                        ClassificationOperation.input_fingerprint
                        == prepared.fingerprint,
                        ClassificationOperation.config_digest == self.runtime.digest,
                    )
                )
            if match is not None:
                return self._view(match)
            latest = session.scalar(
                operations.order_by(
                    ClassificationOperation.created_at.desc(),
                    ClassificationOperation.id.desc(),
                ).limit(1)
            )
            return self._view(latest, matches=False)

    def classify(self, org: UUID, actor: UUID, ticket_id: UUID) -> ClassificationView:
        runtime = self.runtime
        with self.sessions.begin() as session:
            ticket = self._ticket(session, org, actor, ticket_id, lock=True)
            if runtime is None:
                raise ApplicationClassificationError("classification_disabled", 503)
            try:
                prepared = _input(session, ticket)
            except InputTooLargeError:
                raise ApplicationClassificationError("input_too_large", 422) from None
            digest = runtime.digest
            existing = session.scalar(
                select(ClassificationOperation).where(
                    ClassificationOperation.organization_id == org,
                    ClassificationOperation.ticket_id == ticket_id,
                    ClassificationOperation.input_fingerprint == prepared.fingerprint,
                    ClassificationOperation.config_digest == digest,
                )
            )
            if existing is not None:
                view = self._view(existing)
                # GET is read-only; this explicit POST materializes expired claims.
                if view.state == "unknown" and existing.state == "in_progress":
                    existing.state, existing.error_code = (
                        "unknown",
                        "classification_unknown",
                    )
                    existing.completed_at = datetime.now(UTC)
                    self._audit(session, existing)
                existing_view = view
                operation_id = None
            else:
                existing_view = None
                operation_id = uuid4()
                now = datetime.now(UTC)
                session.add(
                    ClassificationOperation(
                        id=operation_id,
                        organization_id=org,
                        ticket_id=ticket_id,
                        requesting_actor_user_id=actor,
                        opening_message_id=prepared.message_id,
                        input_fingerprint=prepared.fingerprint,
                        config_digest=digest,
                        configuration_version=runtime.configuration_version,
                        input_policy_version=INPUT_POLICY_VERSION,
                        taxonomy_version=TAXONOMY_VERSION,
                        schema_version=SCHEMA_VERSION,
                        prompt_version="scripted.v1",
                        adapter_version="fake.v1",
                        provider="fake",
                        model="scripted-no-model",
                        mode="fake",
                        state="in_progress",
                        attempt_count=0 if prepared.insufficient_context else 1,
                        created_at=now,
                        expires_at=now + timedelta(seconds=30),
                    )
                )
        if existing_view is not None:
            return self._return_or_error(existing_view)
        assert operation_id is not None

        # No session, connection or ORM object is used during provider execution.
        start = monotonic()
        result = None
        failure = None
        uncertain = False
        try:
            if prepared.insufficient_context:
                result = ClassificationResult(
                    outcome="insufficient_context", category=None
                )
            else:
                result = parse_provider_output(
                    runtime.provider.classify(prepared.payload)
                )
        except (ProviderTimeoutError, ProviderUnavailableError) as exc:
            failure, uncertain = exc.category, True
        except (
            ProviderRateLimitedError,
            ProviderAuthenticationError,
            InvalidProviderOutputError,
        ) as exc:
            failure = exc.category
        except Exception:
            failure, uncertain = "provider_unavailable", True
        elapsed = max(0, int((monotonic() - start) * 1000))

        with self.sessions.begin() as session:
            # Lock authorization before ticket, as at claim creation. Even if
            # access disappeared, settle the internal claim without returning data.
            authorized = _authorized(session, org, actor, lock=True)
            ticket = session.scalar(
                select(Ticket)
                .where(Ticket.organization_id == org, Ticket.id == ticket_id)
                .with_for_update()
            )
            operation = session.scalar(
                select(ClassificationOperation)
                .where(
                    ClassificationOperation.organization_id == org,
                    ClassificationOperation.id == operation_id,
                )
                .with_for_update()
            )
            assert operation is not None
            if operation.state != "in_progress":
                view = self._view(operation)
            else:
                operation.completed_at = datetime.now(UTC)
                operation.elapsed_ms = elapsed
                changed = ticket is None or runtime.digest != digest
                if ticket is not None:
                    try:
                        changed |= (
                            _input(session, ticket).fingerprint != prepared.fingerprint
                        )
                    except InputTooLargeError:
                        changed = True
                if not authorized:
                    operation.state, operation.error_code = (
                        "failed",
                        "authorization_changed",
                    )
                elif operation.expires_at <= datetime.now(UTC):
                    operation.state, operation.error_code = (
                        "unknown",
                        "classification_unknown",
                    )
                elif changed:
                    operation.state, operation.error_code = "stale", "input_changed"
                elif failure is not None:
                    operation.state = "unknown" if uncertain else "failed"
                    operation.error_code = failure
                else:
                    assert result is not None
                    operation.state = "succeeded"
                    operation.outcome, operation.category = (
                        result.outcome,
                        result.category,
                    )
                self._audit(session, operation)
                view = self._view(operation)
        if not authorized:
            raise ApplicationClassificationError("classification_forbidden", 403)
        return self._return_or_error(view)

    @staticmethod
    def _return_or_error(view: ClassificationView) -> ClassificationView:
        if view.state == "current":
            return view
        code = view.error_code or (
            "classification_in_progress"
            if view.state == "in_progress"
            else "classification_unknown"
        )
        raise ApplicationClassificationError(code, ERROR_STATUS.get(code, 409))

    @staticmethod
    def _audit(session: Session, operation: ClassificationOperation) -> None:
        AuditEventService(
            session, operation.organization_id
        ).record_classification_finished(
            actor_user_id=operation.requesting_actor_user_id,
            operation_id=operation.id,
            state=operation.state,
        )
