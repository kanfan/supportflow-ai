from io import BytesIO
from pathlib import Path
from typing import BinaryIO

import pytest
from pydantic import SecretStr

from app.config import Settings
from app.documents.clamd import ClamdDocumentSafetyScanner
from app.documents.composition import (
    DocumentScannerConfigurationError,
    resolve_document_safety_scanner,
)
from app.documents.ports import (
    DocumentScanResult,
    FakeDocumentSafetyScanner,
)
from app.documents.storage import InMemoryDocumentStorage
from app.main import create_app


class ConfiguredExternalScanner:
    def scan(self, source: BinaryIO) -> DocumentScanResult:
        del source
        return DocumentScanResult.CLEAN


@pytest.mark.parametrize("result", list(DocumentScanResult))
def test_fake_scanner_returns_the_configured_deterministic_result(
    result: DocumentScanResult,
) -> None:
    scanner = FakeDocumentSafetyScanner(result)

    assert scanner.scan(BytesIO(b"untrusted fixture")) is result


def production_settings(storage_root: Path) -> Settings:
    return Settings(
        environment="production",
        database_ssl_root_cert_path=Path("/app/certs/rds-ca-bundle.pem"),
        auth_secret_key=SecretStr("x" * 32),
        document_scanner_mode="external",
        document_storage_mode="s3",
        document_s3_bucket="supportflow-production-documents",
        document_s3_region="eu-central-1",
        document_storage_root=storage_root,
        redis_url=SecretStr("rediss://default:test@cache.internal:6379/0"),
        celery_broker_url=SecretStr("rediss://default:test@cache.internal:6379/1"),
        celery_result_backend_url=SecretStr(
            "rediss://default:test@cache.internal:6379/2"
        ),
    )


def test_production_cannot_boot_with_only_external_mode_label(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        DocumentScannerConfigurationError,
        match="requires a configured scanner adapter",
    ):
        create_app(production_settings(tmp_path))


def test_production_rejects_injected_fake_scanner(tmp_path: Path) -> None:
    with pytest.raises(
        DocumentScannerConfigurationError,
        match="cannot use the fake scanner",
    ):
        create_app(
            production_settings(tmp_path),
            document_safety_scanner=FakeDocumentSafetyScanner(),
        )


def test_production_accepts_concrete_external_scanner(tmp_path: Path) -> None:
    scanner = ConfiguredExternalScanner()

    application = create_app(
        production_settings(tmp_path),
        document_safety_scanner=scanner,
        document_storage=InMemoryDocumentStorage(),
    )

    assert application.state.document_safety_scanner is scanner


def test_fake_scanner_gate_releases_only_after_marker_exists(
    tmp_path: Path,
) -> None:
    gate_path = tmp_path / "release"
    scanner = FakeDocumentSafetyScanner(
        gate_path=gate_path,
        gate_timeout_seconds=1,
    )
    gate_path.touch()

    assert scanner.scan(BytesIO(b"safe")) is DocumentScanResult.CLEAN


def test_fake_scanner_gate_times_out_as_unavailable(tmp_path: Path) -> None:
    scanner = FakeDocumentSafetyScanner(
        gate_path=tmp_path / "missing",
        gate_timeout_seconds=0,
    )

    assert scanner.scan(BytesIO(b"safe")) is DocumentScanResult.UNAVAILABLE


def test_clamd_mode_builds_the_concrete_loopback_adapter() -> None:
    scanner = resolve_document_safety_scanner(
        Settings(environment="test", document_scanner_mode="clamd"),
        None,
    )

    assert isinstance(scanner, ClamdDocumentSafetyScanner)


def test_clamd_mode_rejects_an_arbitrary_injected_external_adapter() -> None:
    with pytest.raises(
        DocumentScannerConfigurationError,
        match="concrete ClamD adapter",
    ):
        resolve_document_safety_scanner(
            Settings(environment="test", document_scanner_mode="clamd"),
            ConfiguredExternalScanner(),
        )
