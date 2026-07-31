from app.config import Settings
from app.documents.ports import (
    DocumentSafetyScanner,
    FakeDocumentSafetyScanner,
)


class DocumentScannerConfigurationError(RuntimeError):
    """Raised when scanner configuration does not match a usable adapter."""


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
        return FakeDocumentSafetyScanner()

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
