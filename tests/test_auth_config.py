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
