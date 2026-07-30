from __future__ import annotations

from enum import StrEnum
from typing import BinaryIO, Protocol
from uuid import UUID


class DocumentStorage(Protocol):
    """Private object storage used by both the API and ingestion worker."""

    def put(self, key: str, source: BinaryIO) -> None: ...

    def open(self, key: str) -> BinaryIO: ...

    def delete(self, key: str) -> None: ...


class DocumentTaskDispatcher(Protocol):
    """Publishes the ID-only ingestion contract without implementing the task."""

    def dispatch(self, document_version_id: UUID) -> None: ...


class DocumentScanResult(StrEnum):
    CLEAN = "clean"
    INFECTED = "infected"
    UNAVAILABLE = "unavailable"


class DocumentSafetyScanner(Protocol):
    """Worker-facing malware/safety boundary implemented fully in Issue #33."""

    def scan(self, source: BinaryIO) -> DocumentScanResult: ...


class FakeDocumentSafetyScanner:
    """Deterministic local/test scanner; forbidden in staging and production."""

    def __init__(
        self,
        result: DocumentScanResult = DocumentScanResult.CLEAN,
    ) -> None:
        self._result = result

    def scan(self, source: BinaryIO) -> DocumentScanResult:
        del source
        return self._result
