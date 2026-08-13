from app.config import Settings
from app.documents.clamd import ClamdDocumentSafetyScanner
from app.documents.ports import (
    DocumentSafetyScanner,
    DocumentStorage,
    FakeDocumentSafetyScanner,
)
from app.documents.s3_storage import S3DocumentStorage, build_s3_client
from app.documents.storage import LocalDocumentStorage


class DocumentScannerConfigurationError(RuntimeError):
    """Raised when scanner configuration does not match a usable adapter."""


class DocumentStorageConfigurationError(RuntimeError):
    """Raised when storage configuration cannot produce a usable adapter."""


def resolve_document_storage(
    settings: Settings,
    configured_storage: DocumentStorage | None,
) -> DocumentStorage:
    if configured_storage is not None:
        return configured_storage
    if settings.document_storage_mode == "local":
        return LocalDocumentStorage(settings.document_storage_root)
    if settings.document_s3_bucket is None or settings.document_s3_region is None:
        raise DocumentStorageConfigurationError(
            "S3 storage requires bucket and region configuration"
        )

    endpoint_url = (
        settings.document_s3_endpoint_url.strip()
        if settings.document_s3_endpoint_url
        and settings.document_s3_endpoint_url.strip()
        else None
    )
    client = build_s3_client(
        region_name=settings.document_s3_region.strip(),
        endpoint_url=endpoint_url,
        connect_timeout_seconds=settings.dependency_connect_timeout_seconds,
        read_timeout_seconds=settings.document_s3_read_timeout_seconds,
        total_max_attempts=settings.document_s3_total_max_attempts,
    )
    return S3DocumentStorage(client, settings.document_s3_bucket.strip())


def close_document_storage(storage: DocumentStorage) -> None:
    if isinstance(storage, S3DocumentStorage):
        storage.close()


def resolve_document_safety_scanner(
    settings: Settings,
    configured_scanner: DocumentSafetyScanner | None,
) -> DocumentSafetyScanner:
    if settings.document_scanner_mode == "fake":
        if configured_scanner is not None:
            if not isinstance(configured_scanner, FakeDocumentSafetyScanner):
                raise DocumentScannerConfigurationError(
                    "Fake scanner mode requires the deterministic fake adapter"
                )
            return configured_scanner
        return FakeDocumentSafetyScanner(
            gate_path=settings.document_fake_scanner_gate_path,
            gate_timeout_seconds=(settings.document_fake_scanner_gate_timeout_seconds),
        )

    if settings.document_scanner_mode == "clamd":
        if configured_scanner is not None:
            if not isinstance(configured_scanner, ClamdDocumentSafetyScanner):
                raise DocumentScannerConfigurationError(
                    "ClamD scanner mode requires the concrete ClamD adapter"
                )
            return configured_scanner
        return ClamdDocumentSafetyScanner(
            host=settings.document_clamd_host,
            port=settings.document_clamd_port,
            connect_timeout_seconds=(settings.document_scanner_connect_timeout_seconds),
            scan_timeout_seconds=settings.document_scanner_scan_timeout_seconds,
            max_stream_bytes=settings.document_max_upload_bytes,
            signature_max_age_seconds=(
                settings.document_scanner_signature_max_age_seconds
            ),
            clock_skew_tolerance_seconds=(
                settings.document_scanner_clock_skew_tolerance_seconds
            ),
        )

    if configured_scanner is None:
        raise DocumentScannerConfigurationError(
            "External scanner mode requires a configured scanner adapter"
        )
    if not isinstance(configured_scanner, DocumentSafetyScanner):
        raise DocumentScannerConfigurationError(
            "Configured scanner does not implement the scanner interface"
        )
    if isinstance(configured_scanner, FakeDocumentSafetyScanner):
        raise DocumentScannerConfigurationError(
            "External scanner mode cannot use the fake scanner adapter"
        )
    return configured_scanner
