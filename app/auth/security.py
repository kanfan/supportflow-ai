from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
from pwdlib import PasswordHash

from app.config import Settings


class InvalidAccessTokenError(ValueError):
    """Raised when an access token cannot be trusted."""


@dataclass(frozen=True)
class AccessToken:
    value: str
    expires_in: int


class PasswordManager:
    """Hash and verify passwords with pwdlib's recommended Argon2id settings."""

    def __init__(self) -> None:
        self._password_hash = PasswordHash.recommended()
        self._dummy_hash = self._password_hash.hash(uuid4().hex)

    def hash(self, password: str) -> str:
        return self._password_hash.hash(password)

    def verify(self, password: str, password_hash: str) -> bool:
        return self._password_hash.verify(password, password_hash)

    def verify_dummy(self, password: str) -> None:
        """Spend hashing work when a login email does not exist."""

        self._password_hash.verify(password, self._dummy_hash)


class AccessTokenManager:
    ALGORITHM = "HS256"
    REQUIRED_CLAIMS = ["sub", "iat", "exp", "iss", "aud", "jti"]

    def __init__(
        self,
        *,
        secret_key: str,
        issuer: str,
        audience: str,
        lifetime: timedelta,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if len(secret_key.encode()) < 32:
            raise ValueError("The authentication secret must contain at least 32 bytes")
        self._secret_key = secret_key
        self._issuer = issuer
        self._audience = audience
        self._lifetime = lifetime
        self._clock = clock or (lambda: datetime.now(UTC))

    @classmethod
    def from_settings(cls, settings: Settings) -> "AccessTokenManager":
        return cls(
            secret_key=settings.auth_secret_key.get_secret_value(),
            issuer=settings.auth_issuer,
            audience=settings.auth_audience,
            lifetime=timedelta(minutes=settings.access_token_ttl_minutes),
        )

    def issue(self, user_id: UUID) -> AccessToken:
        issued_at = self._clock()
        expires_at = issued_at + self._lifetime
        payload = {
            "sub": str(user_id),
            "iat": issued_at,
            "exp": expires_at,
            "iss": self._issuer,
            "aud": self._audience,
            "jti": str(uuid4()),
        }
        value = jwt.encode(payload, self._secret_key, algorithm=self.ALGORITHM)
        return AccessToken(
            value=value,
            expires_in=max(0, int(self._lifetime.total_seconds())),
        )

    def decode_user_id(self, token: str) -> UUID:
        try:
            payload = jwt.decode(
                token,
                self._secret_key,
                algorithms=[self.ALGORITHM],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": self.REQUIRED_CLAIMS},
            )
            subject = payload["sub"]
            token_id = payload["jti"]
            if not isinstance(subject, str) or not isinstance(token_id, str):
                raise InvalidAccessTokenError
            UUID(token_id)
            return UUID(subject)
        except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
            raise InvalidAccessTokenError from exc
