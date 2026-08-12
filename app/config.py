from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


DEVELOPMENT_AUTH_SECRET = "development-only-secret-change-before-production"


def _require_verified_redis_tls(setting_name: str, value: SecretStr) -> None:
    """Reject deployed Redis URLs that can bypass transport verification."""

    parsed = urlsplit(value.get_secret_value())
    if parsed.scheme.lower() != "rediss" or parsed.hostname is None:
        raise ValueError(
            f"SUPPORTFLOW_{setting_name} must use rediss:// with a hostname "
            "outside local/test"
        )

    query = parse_qs(parsed.query, keep_blank_values=True)
    certificate_modes = [item.lower() for item in query.get("ssl_cert_reqs", [])]
    if certificate_modes and certificate_modes != ["required"]:
        raise ValueError(
            f"SUPPORTFLOW_{setting_name} must require Redis certificate verification"
        )
    hostname_modes = [item.lower() for item in query.get("ssl_check_hostname", [])]
    if hostname_modes and hostname_modes != ["true"]:
        raise ValueError(
            f"SUPPORTFLOW_{setting_name} must require Redis hostname verification"
        )


def _require_verified_database_tls(
    database_url: SecretStr,
    ssl_root_cert_path: Path | None,
) -> None:
    """Reject deployed PostgreSQL connections that can bypass verification."""

    parsed = urlsplit(database_url.get_secret_value())
    if parsed.scheme.lower() != "postgresql+psycopg" or parsed.hostname is None:
        raise ValueError(
            "SUPPORTFLOW_DATABASE_URL must use postgresql+psycopg:// with a "
            "hostname outside local/test"
        )

    query = parse_qs(parsed.query, keep_blank_values=True)
    ssl_modes = [item.lower() for item in query.get("sslmode", [])]
    if ssl_modes and ssl_modes != ["verify-full"]:
        raise ValueError(
            "SUPPORTFLOW_DATABASE_URL must not weaken PostgreSQL certificate "
            "or hostname verification"
        )
    if "sslrootcert" in query:
        raise ValueError(
            "Configure the PostgreSQL CA through "
            "SUPPORTFLOW_DATABASE_SSL_ROOT_CERT_PATH, not DATABASE_URL"
        )
    if ssl_root_cert_path is None or str(ssl_root_cert_path).strip() in {"", "."}:
        raise ValueError(
            "SUPPORTFLOW_DATABASE_SSL_ROOT_CERT_PATH is required outside local/test"
        )


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
    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://supportflow:supportflow@localhost:5432/supportflow"
    )
    database_ssl_root_cert_path: Path | None = None
    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")
    dependency_connect_timeout_seconds: int = Field(default=2, ge=1, le=10)
    celery_broker_url: SecretStr = SecretStr("redis://localhost:6379/0")
    celery_result_backend_url: SecretStr = SecretStr("redis://localhost:6379/1")
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
    document_storage_mode: Literal["local", "s3"] = "local"
    document_storage_root: Path = Path(".supportflow/documents")
    document_s3_bucket: str | None = None
    document_s3_region: str | None = None
    document_s3_endpoint_url: str | None = None
    document_s3_read_timeout_seconds: int = Field(default=30, ge=1, le=120)
    document_s3_total_max_attempts: int = Field(default=3, ge=1, le=10)
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
        if (
            self.environment in {"staging", "production"}
            and self.document_storage_mode != "s3"
        ):
            raise ValueError(
                "SUPPORTFLOW_DOCUMENT_STORAGE_MODE must be s3 outside local/test"
            )
        if self.document_storage_mode == "s3" and (
            not self.document_s3_bucket or not self.document_s3_bucket.strip()
        ):
            raise ValueError(
                "SUPPORTFLOW_DOCUMENT_S3_BUCKET is required for S3 storage"
            )
        if self.document_storage_mode == "s3" and (
            not self.document_s3_region or not self.document_s3_region.strip()
        ):
            raise ValueError(
                "SUPPORTFLOW_DOCUMENT_S3_REGION is required for S3 storage"
            )
        if (
            self.environment in {"staging", "production"}
            and self.document_s3_endpoint_url is not None
            and not self.document_s3_endpoint_url.strip().startswith("https://")
        ):
            raise ValueError(
                "SUPPORTFLOW_DOCUMENT_S3_ENDPOINT_URL must use HTTPS outside local/test"
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
        if self.environment in {"staging", "production"}:
            _require_verified_database_tls(
                self.database_url,
                self.database_ssl_root_cert_path,
            )
            _require_verified_redis_tls("REDIS_URL", self.redis_url)
            _require_verified_redis_tls(
                "CELERY_BROKER_URL",
                self.celery_broker_url,
            )
            _require_verified_redis_tls(
                "CELERY_RESULT_BACKEND_URL",
                self.celery_result_backend_url,
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
