from functools import lru_cache
from pathlib import Path
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
    celery_visibility_timeout_seconds: int = Field(
        default=180,
        ge=5,
        le=86_400,
    )
    auth_secret_key: SecretStr = SecretStr(DEVELOPMENT_AUTH_SECRET)
    auth_issuer: str = "supportflow"
    auth_audience: str = "supportflow-api"
    access_token_ttl_minutes: int = Field(default=15, ge=1, le=1440)
    ui_session_ttl_minutes: int = Field(default=480, ge=5, le=1440)
    document_storage_root: Path = Path(".supportflow/documents")
    document_max_upload_bytes: int = Field(
        default=10 * 1024 * 1024,
        ge=1,
        le=10 * 1024 * 1024,
    )
    document_scanner_mode: Literal["fake", "external"] = "fake"
    document_fake_scanner_gate_path: Path | None = None
    document_fake_scanner_gate_timeout_seconds: int = Field(
        default=120,
        ge=1,
        le=300,
    )
    document_max_pdf_pages: int = Field(default=250, ge=1, le=1000)
    document_max_extracted_characters: int = Field(
        default=2_000_000,
        ge=1,
        le=10_000_000,
    )
    document_ingestion_max_retries: int = Field(default=3, ge=0, le=10)
    document_ingestion_soft_time_limit_seconds: int = Field(
        default=60,
        ge=1,
        le=600,
    )
    document_ingestion_hard_time_limit_seconds: int = Field(
        default=75,
        ge=2,
        le=900,
    )

    @model_validator(mode="after")
    def reject_development_secret_outside_local_environments(self) -> "Settings":
        if (
            self.environment in {"staging", "production"}
            and self.auth_secret_key.get_secret_value() == DEVELOPMENT_AUTH_SECRET
        ):
            raise ValueError(
                "SUPPORTFLOW_AUTH_SECRET_KEY must be configured outside local/test"
            )
        if (
            self.environment in {"staging", "production"}
            and self.document_scanner_mode == "fake"
        ):
            raise ValueError(
                "SUPPORTFLOW_DOCUMENT_SCANNER_MODE must not be fake outside local/test"
            )
        if self.document_fake_scanner_gate_path is not None and (
            self.environment not in {"local", "test"}
            or self.document_scanner_mode != "fake"
        ):
            raise ValueError(
                "Document fake scanner gate is restricted to local/test fake mode"
            )
        if (
            self.document_ingestion_hard_time_limit_seconds
            <= self.document_ingestion_soft_time_limit_seconds
        ):
            raise ValueError(
                "Document ingestion hard time limit must exceed the soft time limit"
            )
        if (
            self.environment in {"staging", "production"}
            and self.celery_visibility_timeout_seconds
            <= self.document_ingestion_hard_time_limit_seconds
        ):
            raise ValueError(
                "Celery visibility timeout must exceed the ingestion hard time limit"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
