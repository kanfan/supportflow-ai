from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from app.config import Settings


SECURE_REDIS_URL = "rediss://default:not-a-real-secret@cache.internal:6379/0"
SECURE_CELERY_BROKER_URL = "rediss://default:not-a-real-secret@cache.internal:6379/1"
SECURE_CELERY_BACKEND_URL = "rediss://default:not-a-real-secret@cache.internal:6379/2"
SECURE_DATABASE_URL = (
    "postgresql+psycopg://supportflow:not-a-real-secret@db.internal/supportflow"
)
DATABASE_CA_PATH = Path("/app/certs/rds-ca-bundle.pem")


def deployed_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "production",
        "auth_secret_key": SecretStr("x" * 32),
        "document_scanner_mode": "external",
        "document_storage_mode": "s3",
        "document_s3_bucket": "supportflow-production-documents",
        "document_s3_region": "eu-central-1",
        "database_url": SECURE_DATABASE_URL,
        "database_ssl_root_cert_path": DATABASE_CA_PATH,
        "redis_url": SECURE_REDIS_URL,
        "celery_broker_url": SECURE_CELERY_BROKER_URL,
        "celery_result_backend_url": SECURE_CELERY_BACKEND_URL,
    }
    values.update(overrides)
    return Settings.model_validate(values)


def test_deployed_environment_rejects_development_auth_secret() -> None:
    with pytest.raises(ValidationError, match="AUTH_SECRET_KEY"):
        Settings(environment="production")


def test_deployed_environment_accepts_explicit_auth_secret() -> None:
    settings = deployed_settings()

    assert settings.auth_secret_key.get_secret_value() == "x" * 32


def test_deployed_database_requires_explicit_ca_path() -> None:
    with pytest.raises(ValidationError, match="DATABASE_SSL_ROOT_CERT_PATH"):
        deployed_settings(database_ssl_root_cert_path=None)


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql+psycopg://supportflow:secret@db.internal/supportflow?sslmode=disable",
        "postgresql+psycopg://supportflow:secret@db.internal/supportflow?sslmode=require",
        "postgresql+psycopg://supportflow:secret@db.internal/supportflow?sslmode=verify-ca",
    ],
)
def test_deployed_database_rejects_weakened_tls_modes(database_url: str) -> None:
    with pytest.raises(ValidationError, match="certificate or hostname verification"):
        deployed_settings(database_url=database_url)


def test_deployed_database_rejects_ca_path_in_url() -> None:
    with pytest.raises(ValidationError, match="DATABASE_SSL_ROOT_CERT_PATH"):
        deployed_settings(
            database_url=(
                f"{SECURE_DATABASE_URL}?sslmode=verify-full&sslrootcert=/tmp/ca.pem"
            )
        )


@pytest.mark.parametrize(
    "database_url",
    [
        "sqlite:///supportflow.db",
        "postgresql+psycopg:///supportflow",
    ],
)
def test_deployed_database_requires_psycopg_and_hostname(database_url: str) -> None:
    with pytest.raises(ValidationError, match=r"postgresql\+psycopg"):
        deployed_settings(database_url=database_url)


def test_database_url_is_redacted_from_settings_and_validation_errors() -> None:
    canary = "database-password-canary"
    settings = Settings(
        environment="test",
        database_url=SecretStr(
            f"postgresql+psycopg://supportflow:{canary}@db.internal/supportflow"
        ),
    )

    assert canary not in repr(settings)
    assert canary in settings.database_url.get_secret_value()

    with pytest.raises(ValidationError) as captured:
        deployed_settings(
            database_url=(
                "postgresql+psycopg://supportflow:"
                f"{canary}@db.internal/supportflow?sslmode=disable"
            )
        )

    assert canary not in str(captured.value)


@pytest.mark.parametrize(
    "setting_name",
    ["redis_url", "celery_broker_url", "celery_result_backend_url"],
)
def test_deployed_redis_dependencies_require_tls(setting_name: str) -> None:
    with pytest.raises(ValidationError, match=setting_name.upper()):
        deployed_settings(
            **{setting_name: "redis://default:secret@cache.internal:6379/0"}
        )


@pytest.mark.parametrize(
    ("setting_name", "unsafe_query", "expected_error"),
    [
        ("redis_url", "ssl_cert_reqs=none", "certificate verification"),
        (
            "celery_broker_url",
            "ssl_cert_reqs=optional",
            "certificate verification",
        ),
        (
            "celery_result_backend_url",
            "ssl_check_hostname=false",
            "hostname verification",
        ),
    ],
)
def test_deployed_redis_dependencies_reject_weakened_tls_options(
    setting_name: str,
    unsafe_query: str,
    expected_error: str,
) -> None:
    with pytest.raises(ValidationError, match=expected_error):
        deployed_settings(
            **{
                setting_name: (
                    f"rediss://default:secret@cache.internal:6379/0?{unsafe_query}"
                )
            }
        )


def test_connection_urls_are_redacted_from_settings_representation() -> None:
    canary = "connection-password-canary"
    settings = Settings(
        environment="test",
        redis_url=SecretStr(f"redis://default:{canary}@cache.internal:6379/0"),
        celery_broker_url=SecretStr(f"redis://default:{canary}@cache.internal:6379/1"),
        celery_result_backend_url=SecretStr(
            f"redis://default:{canary}@cache.internal:6379/2"
        ),
    )

    assert canary not in repr(settings)
    assert settings.redis_url.get_secret_value().startswith("redis://")


def test_deployed_tls_validation_error_does_not_echo_connection_secret() -> None:
    canary = "tls-validation-password-canary"

    with pytest.raises(ValidationError) as captured:
        deployed_settings(redis_url=f"redis://default:{canary}@cache.internal:6379/0")

    assert canary not in str(captured.value)


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
            document_storage_mode="s3",
            document_s3_bucket="supportflow-production-documents",
            document_s3_region="eu-central-1",
            document_fake_scanner_gate_path=Path("/tmp/not-production-safe"),
        )


def test_deployed_visibility_timeout_must_exceed_worker_hard_limit() -> None:
    with pytest.raises(ValidationError, match="visibility timeout"):
        Settings(
            environment="production",
            auth_secret_key=SecretStr("x" * 32),
            document_scanner_mode="external",
            document_storage_mode="s3",
            document_s3_bucket="supportflow-production-documents",
            document_s3_region="eu-central-1",
            celery_visibility_timeout_seconds=75,
        )


def test_deployed_environment_rejects_local_document_storage() -> None:
    with pytest.raises(ValidationError, match="DOCUMENT_STORAGE_MODE"):
        Settings(
            environment="staging",
            auth_secret_key=SecretStr("x" * 32),
            document_scanner_mode="external",
        )


@pytest.mark.parametrize(
    ("bucket", "region", "expected_error"),
    [
        (None, "eu-central-1", "DOCUMENT_S3_BUCKET"),
        ("supportflow-documents", None, "DOCUMENT_S3_REGION"),
        ("   ", "eu-central-1", "DOCUMENT_S3_BUCKET"),
        ("supportflow-documents", "   ", "DOCUMENT_S3_REGION"),
    ],
)
def test_s3_storage_requires_nonempty_bucket_and_region(
    bucket: str | None,
    region: str | None,
    expected_error: str,
) -> None:
    with pytest.raises(ValidationError, match=expected_error):
        Settings(
            environment="test",
            document_storage_mode="s3",
            document_s3_bucket=bucket,
            document_s3_region=region,
        )


def test_deployed_s3_custom_endpoint_requires_https() -> None:
    with pytest.raises(ValidationError, match="S3_ENDPOINT_URL must use HTTPS"):
        Settings(
            environment="staging",
            auth_secret_key=SecretStr("x" * 32),
            document_scanner_mode="external",
            document_storage_mode="s3",
            document_s3_bucket="supportflow-staging-documents",
            document_s3_region="eu-central-1",
            document_s3_endpoint_url="http://s3.internal.example",
        )
