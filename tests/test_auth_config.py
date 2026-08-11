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
        document_storage_mode="s3",
        document_s3_bucket="supportflow-production-documents",
        document_s3_region="eu-central-1",
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
