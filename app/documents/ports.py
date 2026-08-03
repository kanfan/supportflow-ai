from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from time import monotonic, sleep
from typing import BinaryIO, Protocol, runtime_checkable
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


@runtime_checkable
class DocumentSafetyScanner(Protocol):
    """Worker-facing malware/safety boundary implemented fully in Issue #33."""

    def scan(self, source: BinaryIO) -> DocumentScanResult: ...


class FakeDocumentSafetyScanner:
    """Deterministic local/test scanner; forbidden in staging and production."""

    def __init__(
        self,
        result: DocumentScanResult = DocumentScanResult.CLEAN,
        *,
        gate_path: Path | None = None,
        gate_timeout_seconds: int = 120,
    ) -> None:
        self._result = result
        self._gate_path = gate_path
        self._gate_timeout_seconds = gate_timeout_seconds

    def scan(self, source: BinaryIO) -> DocumentScanResult:
        del source
        if self._gate_path is not None:
            deadline = monotonic() + self._gate_timeout_seconds
            while not self._gate_path.exists():
                if monotonic() >= deadline:
                    return DocumentScanResult.UNAVAILABLE
                sleep(0.05)
        return self._result
