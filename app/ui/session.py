from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import base64
import hashlib
import hmac
import secrets
from threading import Lock
from uuid import UUID


UI_SESSION_COOKIE = "supportflow_ui_session"


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


class BrowserSessionStore:
    """Small in-process store for the Week 3 UI session adapter."""

    def __init__(
        self,
        *,
        secret_key: str,
        lifetime: timedelta,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if len(secret_key.encode()) < 32:
            raise ValueError("The UI session secret must contain at least 32 bytes")
        self._secret_key = secret_key.encode()
        self._lifetime = lifetime
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sessions: dict[str, BrowserSession] = {}
        self._lock = Lock()

    def create_anonymous(
        self, previous_cookie: str | None = None
    ) -> tuple[str, BrowserSession]:
        self.invalidate(previous_cookie)
        return self._create(user_id=None, organization_id=None)

    def authenticate(
        self,
        *,
        previous_cookie: str | None,
        user_id: UUID,
        organization_id: UUID,
    ) -> tuple[str, BrowserSession]:
        self.invalidate(previous_cookie)
        return self._create(user_id=user_id, organization_id=organization_id)

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

    def _create(
        self,
        *,
        user_id: UUID | None,
        organization_id: UUID | None,
    ) -> tuple[str, BrowserSession]:
        session_id = secrets.token_urlsafe(32)
        session = BrowserSession(
            id=session_id,
            user_id=user_id,
            organization_id=organization_id,
            csrf_token=secrets.token_urlsafe(32),
            expires_at=self._clock() + self._lifetime,
        )
        with self._lock:
            self._sessions[session_id] = session
        return self._signed_cookie(session_id), session

    def _signed_cookie(self, session_id: str) -> str:
        signature = hmac.new(
            self._secret_key,
            f"ui-session:{session_id}".encode(),
            hashlib.sha256,
        ).digest()
        encoded_signature = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
        return f"{session_id}.{encoded_signature}"

    def _verified_session_id(self, cookie_value: str | None) -> str | None:
        if not cookie_value:
            return None
        try:
            session_id, supplied_signature = cookie_value.rsplit(".", 1)
        except ValueError:
            return None
        expected_cookie = self._signed_cookie(session_id)
        expected_signature = expected_cookie.rsplit(".", 1)[1]
        if not hmac.compare_digest(supplied_signature, expected_signature):
            return None
        return session_id
