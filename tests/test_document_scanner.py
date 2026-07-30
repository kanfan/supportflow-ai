from io import BytesIO

import pytest

from app.documents.ports import (
    DocumentScanResult,
    FakeDocumentSafetyScanner,
)


@pytest.mark.parametrize("result", list(DocumentScanResult))
def test_fake_scanner_returns_the_configured_deterministic_result(
    result: DocumentScanResult,
) -> None:
    scanner = FakeDocumentSafetyScanner(result)

    assert scanner.scan(BytesIO(b"untrusted fixture")) is result
