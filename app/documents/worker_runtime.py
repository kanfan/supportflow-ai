from __future__ import annotations

from collections.abc import Callable
import os

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.config import Settings
from app.documents.composition import (
    close_document_storage,
    resolve_document_safety_scanner,
    resolve_document_storage,
)
from app.documents.extraction import DocumentExtractorRegistry, ExtractionLimits
from app.documents.ingestion import DocumentIngestionService
from app.documents.ports import DocumentSafetyScanner, DocumentStorage
from app.infrastructure.database import build_engine, build_session_factory


class DocumentWorkerRuntime:
    """Own ingestion resources within one Celery worker child process."""

    def __init__(
        self,
        settings: Settings,
        *,
        document_safety_scanner: DocumentSafetyScanner | None = None,
        document_storage: DocumentStorage | None = None,
        session_factory: Callable[[], Session] | None = None,
        document_extractors: DocumentExtractorRegistry | None = None,
    ) -> None:
        self._settings = settings
        self._configured_scanner = document_safety_scanner
        self._configured_storage = document_storage
        self._configured_session_factory = session_factory
        self._configured_extractors = document_extractors
        self._owner_pid: int | None = None
        self._owned_engine: Engine | None = None
        self._resolved_storage: DocumentStorage | None = None
        self._service: DocumentIngestionService | None = None

    def initialize_child(self, **_signal_kwargs: object) -> None:
        """Build process-bound clients after Celery prefork creates a child."""

        current_pid = os.getpid()
        if self._service is not None and self._owner_pid == current_pid:
            return
        if self._service is not None:
            self.shutdown_child()

        resolved_scanner = resolve_document_safety_scanner(
            self._settings,
            self._configured_scanner,
        )
        resolved_storage = resolve_document_storage(
            self._settings,
            self._configured_storage,
        )
        owned_engine: Engine | None = None
        try:
            if self._configured_session_factory is None:
                owned_engine = build_engine(
                    self._settings.database_url,
                    connect_timeout_seconds=(
                        self._settings.dependency_connect_timeout_seconds
                    ),
                )
                resolved_session_factory: Callable[[], Session] = build_session_factory(
                    owned_engine
                )
            else:
                resolved_session_factory = self._configured_session_factory

            service = DocumentIngestionService(
                resolved_session_factory,
                storage=resolved_storage,
                scanner=resolved_scanner,
                extractors=(self._configured_extractors or DocumentExtractorRegistry()),
                limits=ExtractionLimits(
                    max_pdf_pages=self._settings.document_max_pdf_pages,
                    max_characters=(self._settings.document_max_extracted_characters),
                ),
            )
        except Exception:
            if self._configured_storage is None:
                close_document_storage(resolved_storage)
            if owned_engine is not None:
                owned_engine.dispose()
            raise

        self._owner_pid = current_pid
        self._owned_engine = owned_engine
        self._resolved_storage = resolved_storage
        self._service = service

    def get_service(self) -> DocumentIngestionService:
        """Resolve lazily for eager/solo execution while preserving process ownership."""

        if self._service is None or self._owner_pid != os.getpid():
            self.initialize_child()
        if self._service is None:  # pragma: no cover - defensive invariant
            raise RuntimeError("Document worker runtime did not initialize")
        return self._service

    def shutdown_child(self, **_signal_kwargs: object) -> None:
        """Release child-owned resources on normal worker-process shutdown."""

        storage = self._resolved_storage
        engine = self._owned_engine
        self._owner_pid = None
        self._owned_engine = None
        self._resolved_storage = None
        self._service = None

        try:
            if storage is not None and self._configured_storage is None:
                close_document_storage(storage)
        finally:
            if engine is not None:
                engine.dispose()
