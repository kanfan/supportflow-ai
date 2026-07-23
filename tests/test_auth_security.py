from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest

from app.auth.security import (
    AccessTokenManager,
    InvalidAccessTokenError,
    PasswordManager,
)


SECRET = "test-secret-with-at-least-thirty-two-bytes"


def build_token_manager(
    *,
    clock: datetime | None = None,
    lifetime: timedelta = timedelta(minutes=15),
) -> AccessTokenManager:
    return AccessTokenManager(
        secret_key=SECRET,
        issuer="supportflow-test",
        audience="supportflow-api-test",
        lifetime=lifetime,
        clock=(lambda: clock) if clock else None,
    )


def test_passwords_use_argon2id_and_verify() -> None:
    manager = PasswordManager()

    password_hash = manager.hash("correct horse battery staple")

    assert password_hash.startswith("$argon2id$")
    assert manager.verify("correct horse battery staple", password_hash)
    assert not manager.verify("incorrect password", password_hash)


def test_access_token_round_trip_uses_user_id_as_subject() -> None:
    manager = build_token_manager()
    user_id = uuid4()

    access_token = manager.issue(user_id)

    assert manager.decode_user_id(access_token.value) == user_id
    assert access_token.expires_in == 900


def test_expired_and_tampered_tokens_are_rejected() -> None:
    expired_manager = build_token_manager(
        clock=datetime.now(UTC) - timedelta(minutes=2),
        lifetime=timedelta(minutes=1),
    )
    expired_token = expired_manager.issue(uuid4()).value

    with pytest.raises(InvalidAccessTokenError):
        expired_manager.decode_user_id(expired_token)

    manager = build_token_manager()
    valid_token = manager.issue(uuid4()).value
    header, payload, signature = valid_token.split(".")
    changed_signature = ("a" if signature[0] != "a" else "b") + signature[1:]

    with pytest.raises(InvalidAccessTokenError):
        manager.decode_user_id(f"{header}.{payload}.{changed_signature}")


def test_tokens_require_all_contract_claims() -> None:
    token = jwt.encode(
        {
            "sub": str(uuid4()),
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(minutes=15),
            "iss": "supportflow-test",
            "aud": "supportflow-api-test",
        },
        SECRET,
        algorithm="HS256",
    )

    with pytest.raises(InvalidAccessTokenError):
        build_token_manager().decode_user_id(token)


def test_short_signing_secret_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least 32 bytes"):
        AccessTokenManager(
            secret_key="too-short",
            issuer="supportflow-test",
            audience="supportflow-api-test",
            lifetime=timedelta(minutes=15),
        )
