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
    )

    assert settings.auth_secret_key.get_secret_value() == "x" * 32
