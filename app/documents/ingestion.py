from __future__ import annotations

from contextlib import closing, contextmanager
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
import logging
from tempfile import SpooledTemporaryFile
from time import perf_counter
from typing import BinaryIO, Callable, cast
from uuid import UUID

from billiard.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.audit.service import AuditEventService
from app.documents.extraction import (
    DocumentExtractorRegistry,
    ExtractionLimits,
    PermanentExtractionError,
)
from app.documents.models import (
    Document,
    DocumentErrorCode,
    DocumentMediaType,
    DocumentProcessingStatus,
    DocumentVersion,
)
from app.documents.ports import (
    DocumentSafetyScanner,
    DocumentScanResult,
    DocumentStorage,
)


logger = logging.getLogger(__name__)
INGEST_DOCUMENT_VERSION_TASK = "supportflow.documents.ingest"


class IngestionOutcome(StrEnum):
    READY = "ready"
    FAILED = "failed"
    ALREADY_TERMINAL = "already_terminal"
    OWNED_BY_ANOTHER_TASK = "owned_by_another_task"
    NOT_FOUND = "not_found"


class RetryableIngestionError(RuntimeError):
    """A safe, classified infrastructure failure eligible for Celery retry."""

    def __init__(self, error_code: DocumentErrorCode) -> None:
        self.error_code = error_code
        super().__init__(error_code.value)


@dataclass(frozen=True)
class ClaimedDocumentVersion:
    organization_id: UUID
    document_id: UUID
    version_id: UUID
    version_number: int
    media_type: DocumentMediaType
    storage_key: str
    size_bytes: int
    content_sha256: str
    attempt_count: int


@dataclass(frozen=True)
class ClaimResult:
    record: ClaimedDocumentVersion | None
    outcome: IngestionOutcome | None


class DocumentIngestionService:
    """Retry-safe worker workflow around one database-owned version UUID."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        storage: DocumentStorage,
        scanner: DocumentSafetyScanner,
        extractors: DocumentExtractorRegistry,
        limits: ExtractionLimits,
        max_attempts: int = 4,
    ) -> None:
        self._session_factory = session_factory
        self._storage = storage
        self._scanner = scanner
        self._extractors = extractors
        self._limits = limits
        self._max_attempts = max_attempts

    @contextmanager
    def delivery_lock(self, version_id: UUID) -> Iterator[bool]:
        # Session-level advisory lock on a dedicated connection, with NO open
        # transaction during extraction. Worker death closes it; duplicate or
        # reconciled messages cannot run concurrently even with the same task ID.
        try:
            with self._session_factory() as session:
                engine = session.get_bind()
                assert isinstance(engine, Engine)
            with engine.connect() as lock:
                acquired = lock.scalar(
                    text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"),
                    {"key": str(version_id)},
                )
                lock.commit()
                if not acquired:
                    yield False
                    return
                try:
                    yield True
                finally:
                    try:
                        lock.execute(
                            text(
                                "SELECT pg_advisory_unlock(hashtextextended(:key, 0))"
                            ),
                            {"key": str(version_id)},
                        )
                        lock.commit()
                    except BaseException:
                        lock.invalidate()  # never return a locked connection to pool
                        raise
        except SQLAlchemyError as exc:
            raise RetryableIngestionError(
                DocumentErrorCode.DATABASE_UNAVAILABLE
            ) from exc

    def process(self, version_id: UUID, task_id: str) -> IngestionOutcome:
        started_at = perf_counter()
        claim = self._claim(version_id, task_id)
        if claim.record is None:
            assert claim.outcome is not None
            self._log_noop(version_id, task_id, claim.outcome)
            return claim.outcome

        record = claim.record
        self._log_transition(record, task_id, "queued_to_extracting")
        try:
            with closing(self._stage_verified_source(record)) as source:
                self._scan(source)
                source.seek(0)
                extracted_text = self._extract(record.media_type, source)
        except PermanentExtractionError as exc:
            self._mark_failed(record, task_id, exc.error_code)
            self._log_terminal(
                record,
                task_id,
                outcome=IngestionOutcome.FAILED,
                error_code=exc.error_code,
                duration_ms=self._duration_ms(started_at),
            )
            return IngestionOutcome.FAILED

        self._mark_ready(record, task_id, extracted_text)
        self._log_terminal(
            record,
            task_id,
            outcome=IngestionOutcome.READY,
            extracted_character_count=len(extracted_text),
            duration_ms=self._duration_ms(started_at),
        )
        return IngestionOutcome.READY

    def prepare_retry(
        self,
        version_id: UUID,
        task_id: str,
        error_code: DocumentErrorCode,
    ) -> None:
        try:
            with self._session_factory() as session:
                row = self._load_locked(session, version_id)
                if row is None:
                    session.rollback()
                    return
                _document, version = row
                if not self._is_owned_extraction(version, task_id):
                    session.rollback()
                    return
                version.status = DocumentProcessingStatus.QUEUED
                version.processing_task_id = None
                version.processing_started_at = None
                version.error_code = error_code.value
                session.commit()
        except SQLAlchemyError as exc:
            raise RetryableIngestionError(
                DocumentErrorCode.DATABASE_UNAVAILABLE
            ) from exc

    def mark_retry_exhausted(self, version_id: UUID, task_id: str) -> None:
        record = self._owned_record(version_id, task_id)
        if record is None:
            return
        self._mark_failed(
            record,
            task_id,
            DocumentErrorCode.RETRY_EXHAUSTED,
        )
        self._log_terminal(
            record,
            task_id,
            outcome=IngestionOutcome.FAILED,
            error_code=DocumentErrorCode.RETRY_EXHAUSTED,
        )

    def _claim(self, version_id: UUID, task_id: str) -> ClaimResult:
        try:
            with self._session_factory() as session:
                row = self._load_locked(session, version_id)
                if row is None:
                    session.rollback()
                    return ClaimResult(None, IngestionOutcome.NOT_FOUND)
                document, version = row
                if version.status in {
                    DocumentProcessingStatus.READY,
                    DocumentProcessingStatus.FAILED,
                }:
                    session.rollback()
                    return ClaimResult(None, IngestionOutcome.ALREADY_TERMINAL)
                if version.status is DocumentProcessingStatus.EXTRACTING:
                    if version.processing_task_id != task_id:
                        session.rollback()
                        return ClaimResult(
                            None,
                            IngestionOutcome.OWNED_BY_ANOTHER_TASK,
                        )
                else:
                    if version.attempt_count >= self._max_attempts:
                        version.status = DocumentProcessingStatus.FAILED
                        version.error_code = DocumentErrorCode.RETRY_EXHAUSTED.value
                        AuditEventService(
                            session, version.organization_id
                        ).record_document_failed(
                            document_version_id=version.id,
                            version_number=version.version_number,
                            error_code=DocumentErrorCode.RETRY_EXHAUSTED,
                            attempt_count=version.attempt_count,
                        )
                        session.commit()
                        return ClaimResult(None, IngestionOutcome.FAILED)
                    version.status = DocumentProcessingStatus.EXTRACTING
                    version.processing_task_id = task_id
                    version.attempt_count += 1
                    version.error_code = None
                    version.processing_started_at = datetime.now(UTC)
                session.flush()
                record = self._record(document, version)
                session.commit()
                return ClaimResult(record, None)
        except SQLAlchemyError as exc:
            raise RetryableIngestionError(
                DocumentErrorCode.DATABASE_UNAVAILABLE
            ) from exc

    def _stage_verified_source(
        self,
        record: ClaimedDocumentVersion,
    ) -> BinaryIO:
        try:
            source = self._storage.open(record.storage_key)
        except Exception as exc:
            raise RetryableIngestionError(
                DocumentErrorCode.STORAGE_UNAVAILABLE
            ) from exc

        staged = SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b")
        digest = sha256()
        size_bytes = 0
        try:
            with closing(source):
                while chunk := source.read(64 * 1024):
                    size_bytes += len(chunk)
                    if size_bytes > record.size_bytes:
                        raise PermanentExtractionError(
                            DocumentErrorCode.EXTRACTION_FAILED
                        )
                    digest.update(chunk)
                    staged.write(chunk)
        except PermanentExtractionError:
            staged.close()
            raise
        except Exception as exc:
            staged.close()
            raise RetryableIngestionError(
                DocumentErrorCode.STORAGE_UNAVAILABLE
            ) from exc
        if (
            size_bytes != record.size_bytes
            or digest.hexdigest() != record.content_sha256
        ):
            staged.close()
            raise PermanentExtractionError(DocumentErrorCode.EXTRACTION_FAILED)
        staged.seek(0)
        return cast(BinaryIO, staged)

    def _scan(self, source: BinaryIO) -> None:
        try:
            result = self._scanner.scan(source)
        except Exception as exc:
            raise RetryableIngestionError(
                DocumentErrorCode.SCANNER_UNAVAILABLE
            ) from exc
        if result is DocumentScanResult.UNAVAILABLE:
            raise RetryableIngestionError(DocumentErrorCode.SCANNER_UNAVAILABLE)
        if result is DocumentScanResult.INFECTED:
            raise PermanentExtractionError(DocumentErrorCode.MALWARE_DETECTED)
        if result is not DocumentScanResult.CLEAN:
            raise RetryableIngestionError(DocumentErrorCode.SCANNER_UNAVAILABLE)

    def _extract(
        self,
        media_type: DocumentMediaType,
        source: BinaryIO,
    ) -> str:
        try:
            extractor = self._extractors.get(media_type)
            return extractor.extract(source, self._limits)
        except PermanentExtractionError:
            raise
        except SoftTimeLimitExceeded as exc:
            raise PermanentExtractionError(
                DocumentErrorCode.EXTRACTION_LIMIT_EXCEEDED
            ) from exc
        except Exception as exc:
            raise PermanentExtractionError(DocumentErrorCode.EXTRACTION_FAILED) from exc

    def _mark_ready(
        self,
        record: ClaimedDocumentVersion,
        task_id: str,
        extracted_text: str,
    ) -> None:
        try:
            with self._session_factory() as session:
                row = self._load_locked(session, record.version_id)
                if row is None or not self._is_owned_extraction(row[1], task_id):
                    session.rollback()
                    return
                _document, version = row
                version.status = DocumentProcessingStatus.READY
                version.extracted_text = extracted_text
                version.error_code = None
                version.processing_task_id = None
                version.processing_started_at = None
                AuditEventService(
                    session, record.organization_id
                ).record_document_ready(
                    document_version_id=record.version_id,
                    version_number=record.version_number,
                    attempt_count=version.attempt_count,
                    extracted_character_count=len(extracted_text),
                )
                session.commit()
        except SQLAlchemyError as exc:
            raise RetryableIngestionError(
                DocumentErrorCode.DATABASE_UNAVAILABLE
            ) from exc

    def _mark_failed(
        self,
        record: ClaimedDocumentVersion,
        task_id: str,
        error_code: DocumentErrorCode,
    ) -> None:
        try:
            with self._session_factory() as session:
                row = self._load_locked(session, record.version_id)
                if row is None or not self._is_owned_extraction(row[1], task_id):
                    session.rollback()
                    return
                _document, version = row
                version.status = DocumentProcessingStatus.FAILED
                version.extracted_text = None
                version.error_code = error_code.value
                version.processing_task_id = None
                version.processing_started_at = None
                AuditEventService(
                    session, record.organization_id
                ).record_document_failed(
                    document_version_id=record.version_id,
                    version_number=record.version_number,
                    error_code=error_code,
                    attempt_count=version.attempt_count,
                )
                session.commit()
        except SQLAlchemyError as exc:
            raise RetryableIngestionError(
                DocumentErrorCode.DATABASE_UNAVAILABLE
            ) from exc

    def _owned_record(
        self,
        version_id: UUID,
        task_id: str,
    ) -> ClaimedDocumentVersion | None:
        try:
            with self._session_factory() as session:
                row = self._load_locked(session, version_id)
                if row is None or not self._is_owned_extraction(row[1], task_id):
                    session.rollback()
                    return None
                record = self._record(*row)
                session.rollback()
                return record
        except SQLAlchemyError as exc:
            raise RetryableIngestionError(
                DocumentErrorCode.DATABASE_UNAVAILABLE
            ) from exc

    @staticmethod
    def _load_locked(
        session: Session,
        version_id: UUID,
    ) -> tuple[Document, DocumentVersion] | None:
        row = session.execute(
            select(Document, DocumentVersion)
            .join(
                DocumentVersion,
                (DocumentVersion.organization_id == Document.organization_id)
                & (DocumentVersion.document_id == Document.id),
            )
            .where(DocumentVersion.id == version_id)
            .with_for_update()
        ).one_or_none()
        if row is None:
            return None
        document, version = row
        return document, version

    @staticmethod
    def _record(
        document: Document,
        version: DocumentVersion,
    ) -> ClaimedDocumentVersion:
        return ClaimedDocumentVersion(
            organization_id=document.organization_id,
            document_id=document.id,
            version_id=version.id,
            version_number=version.version_number,
            media_type=version.media_type,
            storage_key=version.storage_key,
            size_bytes=version.size_bytes,
            content_sha256=version.content_sha256,
            attempt_count=version.attempt_count,
        )

    @staticmethod
    def _is_owned_extraction(version: DocumentVersion, task_id: str) -> bool:
        return (
            version.status is DocumentProcessingStatus.EXTRACTING
            and version.processing_task_id == task_id
        )

    @staticmethod
    def _duration_ms(started_at: float) -> int:
        return max(0, round((perf_counter() - started_at) * 1000))

    @staticmethod
    def _log_transition(
        record: ClaimedDocumentVersion,
        task_id: str,
        transition: str,
    ) -> None:
        logger.info(
            "document_ingestion_transition task_name=%s task_id=%s "
            "organization_id=%s document_id=%s version_id=%s attempt=%s "
            "state_transition=%s",
            INGEST_DOCUMENT_VERSION_TASK,
            task_id,
            record.organization_id,
            record.document_id,
            record.version_id,
            record.attempt_count,
            transition,
        )

    @staticmethod
    def _log_terminal(
        record: ClaimedDocumentVersion,
        task_id: str,
        *,
        outcome: IngestionOutcome,
        error_code: DocumentErrorCode | None = None,
        duration_ms: int | None = None,
        extracted_character_count: int | None = None,
    ) -> None:
        logger.info(
            "document_ingestion_terminal task_name=%s task_id=%s "
            "organization_id=%s document_id=%s version_id=%s attempt=%s "
            "state_transition=extracting_to_%s error_category=%s "
            "duration_ms=%s extracted_character_count=%s",
            INGEST_DOCUMENT_VERSION_TASK,
            task_id,
            record.organization_id,
            record.document_id,
            record.version_id,
            record.attempt_count,
            outcome.value,
            error_code.value if error_code is not None else "none",
            duration_ms if duration_ms is not None else 0,
            (extracted_character_count if extracted_character_count is not None else 0),
        )

    @staticmethod
    def _log_noop(
        version_id: UUID,
        task_id: str,
        outcome: IngestionOutcome,
    ) -> None:
        logger.info(
            "document_ingestion_noop task_name=%s task_id=%s version_id=%s "
            "state_transition=%s",
            INGEST_DOCUMENT_VERSION_TASK,
            task_id,
            version_id,
            outcome.value,
        )
