from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from app.config import Settings


def test_deployed_environment_rejects_development_auth_secret() -> None:
    with pytest.raises(ValidationError, match="AUTH_SECRET_KEY"):
        Settings(environment="production")


def test_deployed_environment_accepts_explicit_auth_secret() -> None:
    settings = Settings(
        environment="production",
        auth_secret_key=SecretStr("x" * 32),
        document_scanner_mode="external",
    )

    assert settings.auth_secret_key.get_secret_value() == "x" * 32


def test_deployed_environment_rejects_fake_document_scanner() -> None:
    with pytest.raises(ValidationError, match="DOCUMENT_SCANNER_MODE"):
        Settings(
            environment="staging",
            auth_secret_key=SecretStr("x" * 32),
        )


def test_document_worker_hard_limit_must_exceed_soft_limit() -> None:
    with pytest.raises(ValidationError, match="hard time limit"):
        Settings(
            environment="test",
            document_ingestion_soft_time_limit_seconds=60,
            document_ingestion_hard_time_limit_seconds=60,
        )


def test_fake_scanner_gate_is_rejected_outside_local_test_fake_mode() -> None:
    with pytest.raises(ValidationError, match="fake scanner gate"):
        Settings(
            environment="production",
            auth_secret_key=SecretStr("x" * 32),
            document_scanner_mode="external",
            document_fake_scanner_gate_path=Path("/tmp/not-production-safe"),
        )


def test_deployed_visibility_timeout_must_exceed_worker_hard_limit() -> None:
    with pytest.raises(ValidationError, match="visibility timeout"):
        Settings(
            environment="production",
            auth_secret_key=SecretStr("x" * 32),
            document_scanner_mode="external",
            celery_visibility_timeout_seconds=75,
        )
