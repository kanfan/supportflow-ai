from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import base64
import hashlib
import hmac
import json
import re
import secrets
from threading import Lock
from typing import Protocol, cast
from uuid import UUID

from redis import Redis
from redis.exceptions import RedisError


UI_SESSION_COOKIE = "supportflow_ui_session"
DEFAULT_UI_SESSION_KEY_PREFIX = "supportflow:ui-session:v1:"
_SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")


@dataclass(frozen=True)
class BrowserSession:
    id: str
    user_id: UUID | None
    organization_id: UUID | None
    csrf_token: str
    expires_at: datetime

    @property
    def is_authenticated(self) -> bool:
        return self.user_id is not None and self.organization_id is not None


class BrowserSessionStore(Protocol):
    """Server-side browser-session boundary used by the agent UI."""

    def create_anonymous(
        self, previous_cookie: str | None = None
    ) -> tuple[str, BrowserSession]: ...

    def authenticate(
        self,
        *,
        previous_cookie: str | None,
        user_id: UUID,
        organization_id: UUID,
    ) -> tuple[str, BrowserSession]: ...

    def resolve(self, cookie_value: str | None) -> BrowserSession | None: ...

    def invalidate(self, cookie_value: str | None) -> None: ...


class BrowserSessionStoreUnavailableError(RuntimeError):
    """Stable failure raised when the shared session backend is unavailable."""


class RedisSessionPipeline(Protocol):
    def delete(self, *names: str) -> object: ...

    def set(self, name: str, value: str, *, ex: int) -> object: ...

    def execute(self) -> list[object]: ...


class RedisSessionClient(Protocol):
    def get(self, name: str) -> str | bytes | None: ...

    def delete(self, *names: str) -> int: ...

    def pipeline(self, *, transaction: bool) -> RedisSessionPipeline: ...


class _SignedBrowserSessionStore:
    def __init__(
        self,
        *,
        secret_key: str,
        lifetime: timedelta,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if len(secret_key.encode()) < 32:
            raise ValueError("The UI session secret must contain at least 32 bytes")
        lifetime_seconds = int(lifetime.total_seconds())
        if lifetime_seconds < 1:
            raise ValueError("The UI session lifetime must be at least one second")
        self._secret_key = secret_key.encode()
        self._lifetime = lifetime
        self._lifetime_seconds = lifetime_seconds
        self._clock = clock or (lambda: datetime.now(UTC))

    def _new_session(
        self,
        *,
        user_id: UUID | None,
        organization_id: UUID | None,
    ) -> BrowserSession:
        return BrowserSession(
            id=secrets.token_urlsafe(32),
            user_id=user_id,
            organization_id=organization_id,
            csrf_token=secrets.token_urlsafe(32),
            expires_at=self._clock() + self._lifetime,
        )

    def _signed_cookie(self, session_id: str) -> str:
        signature = hmac.new(
            self._secret_key,
            f"ui-session:{session_id}".encode(),
            hashlib.sha256,
        ).digest()
        encoded_signature = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
        return f"{session_id}.{encoded_signature}"

    def _verified_session_id(self, cookie_value: str | None) -> str | None:
        if not cookie_value or len(cookie_value) > 128:
            return None
        try:
            session_id, supplied_signature = cookie_value.rsplit(".", 1)
        except ValueError:
            return None
        if _SESSION_ID_PATTERN.fullmatch(session_id) is None:
            return None
        expected_signature = self._signed_cookie(session_id).rsplit(".", 1)[1]
        if not hmac.compare_digest(supplied_signature, expected_signature):
            return None
        return session_id


class InMemoryBrowserSessionStore(_SignedBrowserSessionStore):
    """Deterministic process-local adapter used only by tests."""

    def __init__(
        self,
        *,
        secret_key: str,
        lifetime: timedelta,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(secret_key=secret_key, lifetime=lifetime, clock=clock)
        self._sessions: dict[str, BrowserSession] = {}
        self._lock = Lock()

    def create_anonymous(
        self, previous_cookie: str | None = None
    ) -> tuple[str, BrowserSession]:
        return self._rotate(
            previous_cookie=previous_cookie,
            user_id=None,
            organization_id=None,
        )

    def authenticate(
        self,
        *,
        previous_cookie: str | None,
        user_id: UUID,
        organization_id: UUID,
    ) -> tuple[str, BrowserSession]:
        return self._rotate(
            previous_cookie=previous_cookie,
            user_id=user_id,
            organization_id=organization_id,
        )

    def resolve(self, cookie_value: str | None) -> BrowserSession | None:
        session_id = self._verified_session_id(cookie_value)
        if session_id is None:
            return None
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            if session.expires_at <= self._clock():
                self._sessions.pop(session_id, None)
                return None
            return session

    def invalidate(self, cookie_value: str | None) -> None:
        session_id = self._verified_session_id(cookie_value)
        if session_id is None:
            return
        with self._lock:
            self._sessions.pop(session_id, None)

    def _rotate(
        self,
        *,
        previous_cookie: str | None,
        user_id: UUID | None,
        organization_id: UUID | None,
    ) -> tuple[str, BrowserSession]:
        previous_session_id = self._verified_session_id(previous_cookie)
        session = self._new_session(
            user_id=user_id,
            organization_id=organization_id,
        )
        with self._lock:
            if previous_session_id is not None:
                self._sessions.pop(previous_session_id, None)
            self._sessions[session.id] = session
        return self._signed_cookie(session.id), session


class RedisBrowserSessionStore(_SignedBrowserSessionStore):
    """Shared Redis adapter for browser sessions across API tasks."""

    def __init__(
        self,
        *,
        client: RedisSessionClient,
        secret_key: str,
        lifetime: timedelta,
        key_prefix: str = DEFAULT_UI_SESSION_KEY_PREFIX,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(secret_key=secret_key, lifetime=lifetime, clock=clock)
        if not key_prefix or not key_prefix.endswith(":"):
            raise ValueError(
                "The UI session key prefix must be non-empty and end in ':'"
            )
        self._client = client
        self._key_prefix = key_prefix

    @classmethod
    def from_url(
        cls,
        *,
        redis_url: str,
        secret_key: str,
        lifetime: timedelta,
        key_prefix: str = DEFAULT_UI_SESSION_KEY_PREFIX,
        clock: Callable[[], datetime] | None = None,
    ) -> RedisBrowserSessionStore:
        client = cast(
            RedisSessionClient,
            Redis.from_url(
                redis_url,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            ),
        )
        return cls(
            client=client,
            secret_key=secret_key,
            lifetime=lifetime,
            key_prefix=key_prefix,
            clock=clock,
        )

    def create_anonymous(
        self, previous_cookie: str | None = None
    ) -> tuple[str, BrowserSession]:
        return self._rotate(
            previous_cookie=previous_cookie,
            user_id=None,
            organization_id=None,
        )

    def authenticate(
        self,
        *,
        previous_cookie: str | None,
        user_id: UUID,
        organization_id: UUID,
    ) -> tuple[str, BrowserSession]:
        return self._rotate(
            previous_cookie=previous_cookie,
            user_id=user_id,
            organization_id=organization_id,
        )

    def resolve(self, cookie_value: str | None) -> BrowserSession | None:
        session_id = self._verified_session_id(cookie_value)
        if session_id is None:
            return None
        try:
            stored_value = self._client.get(self._key(session_id))
        except RedisError:
            raise BrowserSessionStoreUnavailableError(
                "Browser session storage is unavailable"
            ) from None
        session = self._deserialize(session_id, stored_value)
        if session is None:
            if stored_value is not None:
                self._delete(session_id)
            return None
        if session.expires_at <= self._clock():
            self._delete(session_id)
            return None
        return session

    def invalidate(self, cookie_value: str | None) -> None:
        session_id = self._verified_session_id(cookie_value)
        if session_id is not None:
            self._delete(session_id)

    def _rotate(
        self,
        *,
        previous_cookie: str | None,
        user_id: UUID | None,
        organization_id: UUID | None,
    ) -> tuple[str, BrowserSession]:
        previous_session_id = self._verified_session_id(previous_cookie)
        session = self._new_session(
            user_id=user_id,
            organization_id=organization_id,
        )
        try:
            pipeline = self._client.pipeline(transaction=True)
            if previous_session_id is not None:
                pipeline.delete(self._key(previous_session_id))
            pipeline.set(
                self._key(session.id),
                self._serialize(session),
                ex=self._lifetime_seconds,
            )
            pipeline.execute()
        except RedisError:
            raise BrowserSessionStoreUnavailableError(
                "Browser session storage is unavailable"
            ) from None
        return self._signed_cookie(session.id), session

    def _delete(self, session_id: str) -> None:
        try:
            self._client.delete(self._key(session_id))
        except RedisError:
            raise BrowserSessionStoreUnavailableError(
                "Browser session storage is unavailable"
            ) from None

    def _key(self, session_id: str) -> str:
        return f"{self._key_prefix}{session_id}"

    @staticmethod
    def _serialize(session: BrowserSession) -> str:
        return json.dumps(
            {
                "csrf_token": session.csrf_token,
                "expires_at": session.expires_at.isoformat(),
                "organization_id": (
                    str(session.organization_id)
                    if session.organization_id is not None
                    else None
                ),
                "user_id": str(session.user_id)
                if session.user_id is not None
                else None,
            },
            separators=(",", ":"),
            sort_keys=True,
        )

    @staticmethod
    def _deserialize(
        session_id: str,
        stored_value: str | bytes | None,
    ) -> BrowserSession | None:
        if stored_value is None:
            return None
        if isinstance(stored_value, bytes):
            try:
                stored_value = stored_value.decode("utf-8")
            except UnicodeDecodeError:
                return None
        try:
            payload = json.loads(stored_value)
        except (json.JSONDecodeError, TypeError):
            return None
        expected_keys = {"csrf_token", "expires_at", "organization_id", "user_id"}
        if not isinstance(payload, dict) or set(payload) != expected_keys:
            return None
        csrf_token = payload["csrf_token"]
        expires_at_value = payload["expires_at"]
        user_id_value = payload["user_id"]
        organization_id_value = payload["organization_id"]
        if not isinstance(csrf_token, str) or not csrf_token:
            return None
        if not isinstance(expires_at_value, str):
            return None
        if user_id_value is not None and not isinstance(user_id_value, str):
            return None
        if organization_id_value is not None and not isinstance(
            organization_id_value, str
        ):
            return None
        if (user_id_value is None) != (organization_id_value is None):
            return None
        try:
            expires_at = datetime.fromisoformat(expires_at_value)
            user_id = UUID(user_id_value) if user_id_value is not None else None
            organization_id = (
                UUID(organization_id_value)
                if organization_id_value is not None
                else None
            )
        except ValueError:
            return None
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            return None
        return BrowserSession(
            id=session_id,
            user_id=user_id,
            organization_id=organization_id,
            csrf_token=csrf_token,
            expires_at=expires_at,
        )
