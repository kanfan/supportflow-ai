from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


DEVELOPMENT_AUTH_SECRET = "development-only-secret-change-before-production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="SUPPORTFLOW_",
        extra="ignore",
        validate_default=True,
    )

    app_name: str = "SupportFlow AI"
    app_version: str = "0.1.0"
    environment: Literal["local", "test", "staging", "production"] = "local"
    debug: bool = False
    database_url: str = (
        "postgresql+psycopg://supportflow:supportflow@localhost:5432/supportflow"
    )
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend_url: str = "redis://localhost:6379/1"
    auth_secret_key: SecretStr = SecretStr(DEVELOPMENT_AUTH_SECRET)
    auth_issuer: str = "supportflow"
    auth_audience: str = "supportflow-api"
    access_token_ttl_minutes: int = Field(default=15, ge=1, le=1440)

    @model_validator(mode="after")
    def reject_development_secret_outside_local_environments(self) -> "Settings":
        if (
            self.environment in {"staging", "production"}
            and self.auth_secret_key.get_secret_value() == DEVELOPMENT_AUTH_SECRET
        ):
            raise ValueError(
                "SUPPORTFLOW_AUTH_SECRET_KEY must be configured outside local/test"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
