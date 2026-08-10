from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import logging
from threading import Event
from typing import BinaryIO
from uuid import UUID, uuid4

from celery import Celery
import pytest
from pypdf import PdfWriter
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.audit.models import AuditAction, AuditEvent
from app.audit.service import AuditEventService
from app.documents.extraction import DocumentExtractorRegistry, ExtractionLimits
from app.documents.ingestion import (
    DocumentIngestionService,
    IngestionOutcome,
    RetryableIngestionError,
)
from app.documents.models import (
    Document,
    DocumentErrorCode,
    DocumentMediaType,
    DocumentProcessingStatus,
    DocumentVersion,
)
from app.documents.ports import (
    DocumentScanResult,
    FakeDocumentSafetyScanner,
)
from app.documents.storage import InMemoryDocumentStorage
from app.documents.tasks import register_document_ingestion_task
from app.identity.models import MembershipRole, Organization, OrganizationMember, User
from app.infrastructure.database import build_session_factory


pytestmark = pytest.mark.integration
TEST_AUTH_SECRET = "ingestion-integration-secret-with-thirty-two-bytes"
ORIGINAL_RECORD_DOCUMENT_READY = AuditEventService.record_document_ready
ORIGINAL_RECORD_DOCUMENT_FAILED = AuditEventService.record_document_failed


def blank_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    destination = BytesIO()
    writer.write(destination)
    return destination.getvalue()


@dataclass(frozen=True)
class PersistedVersion:
    organization_id: UUID
    document_id: UUID
    version_id: UUID
    storage_key: str


def create_queued_version(
    engine: Engine,
    storage: InMemoryDocumentStorage,
    *,
    media_type: DocumentMediaType = DocumentMediaType.TEXT,
    content: bytes = b"SupportFlow ingestion content",
    filename: str = "private-customer-guide.txt",
) -> PersistedVersion:
    organization = Organization(
        name=f"Ingestion {uuid4().hex}",
        slug=f"ingestion-{uuid4().hex}",
    )
    user = User(
        email=f"ingestion-{uuid4().hex}@example.com",
        password_hash="not-a-real-password-hash",
    )
    membership = OrganizationMember(
        organization=organization,
        user=user,
        role=MembershipRole.ADMIN,
    )
    version_id = uuid4()

    with Session(engine, expire_on_commit=False) as session:
        session.add(membership)
        session.flush()
        document = Document(
            organization_id=organization.id,
            created_by_user_id=user.id,
            display_filename=filename,
        )
        session.add(document)
        session.flush()
        storage_key = (
            f"organizations/{organization.id}/documents/{document.id}/"
            f"versions/{version_id}"
        )
        version = DocumentVersion(
            id=version_id,
            organization_id=organization.id,
            document_id=document.id,
            version_number=1,
            media_type=media_type,
            size_bytes=len(content),
            content_sha256=sha256(content).hexdigest(),
            storage_key=storage_key,
        )
        session.add(version)
        session.commit()

    storage.put(storage_key, BytesIO(content))
    return PersistedVersion(
        organization_id=organization.id,
        document_id=document.id,
        version_id=version_id,
        storage_key=storage_key,
    )


def ingestion_service(
    engine: Engine,
    storage: InMemoryDocumentStorage,
    *,
    scanner: FakeDocumentSafetyScanner | None = None,
) -> DocumentIngestionService:
    return DocumentIngestionService(
        build_session_factory(engine),
        storage=storage,
        scanner=scanner or FakeDocumentSafetyScanner(),
        extractors=DocumentExtractorRegistry(),
        limits=ExtractionLimits(max_pdf_pages=250, max_characters=2_000_000),
    )


@pytest.mark.parametrize(
    ("media_type", "content", "expected_text"),
    [
        (DocumentMediaType.TEXT, "Türkçe support".encode(), "Türkçe support"),
        (DocumentMediaType.MARKDOWN, b"# Support\nSafe body", "# Support\nSafe body"),
        (DocumentMediaType.PDF, blank_pdf(), ""),
    ],
)
def test_first_delivery_reaches_ready_for_each_supported_type(
    database_engine: Engine,
    media_type: DocumentMediaType,
    content: bytes,
    expected_text: str,
) -> None:
    storage = InMemoryDocumentStorage()
    persisted = create_queued_version(
        database_engine,
        storage,
        media_type=media_type,
        content=content,
    )

    outcome = ingestion_service(database_engine, storage).process(
        persisted.version_id,
        "first-delivery",
    )

    with Session(database_engine) as session:
        version = session.get(DocumentVersion, persisted.version_id)
        terminal_events = list(
            session.scalars(
                select(AuditEvent).where(
                    AuditEvent.resource_id == persisted.version_id,
                    AuditEvent.action == AuditAction.DOCUMENT_READY.value,
                )
            )
        )
    assert outcome is IngestionOutcome.READY
    assert version is not None
    assert version.status is DocumentProcessingStatus.READY
    assert version.extracted_text == expected_text
    assert version.attempt_count == 1
    assert version.processing_task_id is None
    assert version.processing_started_at is None
    assert version.error_code is None
    assert len(terminal_events) == 1
    assert terminal_events[0].actor_user_id is None
    assert terminal_events[0].event_metadata == {
        "version_number": 1,
        "attempt_count": 1,
        "extracted_character_count": len(expected_text),
    }


def test_sequential_duplicate_is_terminal_noop_without_duplicate_effects(
    database_engine: Engine,
) -> None:
    storage = InMemoryDocumentStorage()
    persisted = create_queued_version(database_engine, storage)
    service = ingestion_service(database_engine, storage)

    first = service.process(persisted.version_id, "delivery-one")
    duplicate = service.process(persisted.version_id, "delivery-two")

    with Session(database_engine) as session:
        version = session.get(DocumentVersion, persisted.version_id)
        version_count = session.scalar(
            select(func.count()).select_from(DocumentVersion)
        )
        ready_event_count = session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == AuditAction.DOCUMENT_READY.value)
        )
    assert first is IngestionOutcome.READY
    assert duplicate is IngestionOutcome.ALREADY_TERMINAL
    assert version is not None
    assert version.attempt_count == 1
    assert version_count == 1
    assert ready_event_count == 1


class BlockingScanner(FakeDocumentSafetyScanner):
    def __init__(self) -> None:
        super().__init__()
        self.started = Event()
        self.release = Event()

    def scan(self, source: BinaryIO) -> DocumentScanResult:
        del source
        self.started.set()
        if not self.release.wait(timeout=10):
            raise RuntimeError("test synchronization timeout")
        return DocumentScanResult.CLEAN


class SequenceScanner(FakeDocumentSafetyScanner):
    def __init__(self, results: list[DocumentScanResult]) -> None:
        super().__init__()
        self.results = results
        self.calls = 0

    def scan(self, source: BinaryIO) -> DocumentScanResult:
        del source
        result = self.results[min(self.calls, len(self.results) - 1)]
        self.calls += 1
        return result


class RetryStateRecordingService(DocumentIngestionService):
    def __init__(
        self,
        engine: Engine,
        storage: InMemoryDocumentStorage,
        scanner: SequenceScanner,
    ) -> None:
        super().__init__(
            build_session_factory(engine),
            storage=storage,
            scanner=scanner,
            extractors=DocumentExtractorRegistry(),
            limits=ExtractionLimits(
                max_pdf_pages=250,
                max_characters=2_000_000,
            ),
        )
        self._engine = engine
        self.released_states: list[
            tuple[DocumentProcessingStatus, int, str | None, str | None]
        ] = []

    def prepare_retry(
        self,
        version_id: UUID,
        task_id: str,
        error_code: DocumentErrorCode,
    ) -> None:
        super().prepare_retry(version_id, task_id, error_code)
        with Session(self._engine) as session:
            version = session.get(DocumentVersion, version_id)
            assert version is not None
            self.released_states.append(
                (
                    version.status,
                    version.attempt_count,
                    version.processing_task_id,
                    version.error_code,
                )
            )


def test_concurrent_duplicate_cannot_take_an_active_claim(
    database_engine: Engine,
) -> None:
    storage = InMemoryDocumentStorage()
    persisted = create_queued_version(database_engine, storage)
    scanner = BlockingScanner()
    service = ingestion_service(database_engine, storage, scanner=scanner)

    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(
            service.process,
            persisted.version_id,
            "active-delivery",
        )
        assert scanner.started.wait(timeout=10)
        duplicate = service.process(persisted.version_id, "duplicate-delivery")
        scanner.release.set()
        assert first.result(timeout=10) is IngestionOutcome.READY

    with Session(database_engine) as session:
        version = session.get(DocumentVersion, persisted.version_id)
        ready_events = session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == AuditAction.DOCUMENT_READY.value)
        )
    assert duplicate is IngestionOutcome.OWNED_BY_ANOTHER_TASK
    assert version is not None
    assert version.attempt_count == 1
    assert ready_events == 1


def test_same_task_redelivery_resumes_claim_without_incrementing_attempt(
    database_engine: Engine,
) -> None:
    storage = InMemoryDocumentStorage()
    persisted = create_queued_version(database_engine, storage)
    with Session(database_engine) as session:
        version = session.get(DocumentVersion, persisted.version_id)
        assert version is not None
        version.status = DocumentProcessingStatus.EXTRACTING
        version.processing_task_id = "redelivered-task"
        version.attempt_count = 1
        session.commit()

    outcome = ingestion_service(database_engine, storage).process(
        persisted.version_id,
        "redelivered-task",
    )

    with Session(database_engine) as session:
        version = session.get(DocumentVersion, persisted.version_id)
    assert outcome is IngestionOutcome.READY
    assert version is not None
    assert version.status is DocumentProcessingStatus.READY
    assert version.attempt_count == 1


def test_retryable_scanner_failure_returns_claim_to_queued(
    database_engine: Engine,
) -> None:
    storage = InMemoryDocumentStorage()
    persisted = create_queued_version(database_engine, storage)
    service = ingestion_service(
        database_engine,
        storage,
        scanner=FakeDocumentSafetyScanner(DocumentScanResult.UNAVAILABLE),
    )

    with pytest.raises(RetryableIngestionError) as captured:
        service.process(persisted.version_id, "retryable-delivery")
    service.prepare_retry(
        persisted.version_id,
        "retryable-delivery",
        captured.value.error_code,
    )

    with Session(database_engine) as session:
        version = session.get(DocumentVersion, persisted.version_id)
        terminal_events = session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.action.in_(
                    [
                        AuditAction.DOCUMENT_READY.value,
                        AuditAction.DOCUMENT_FAILED.value,
                    ]
                )
            )
        )
    assert captured.value.error_code is DocumentErrorCode.SCANNER_UNAVAILABLE
    assert version is not None
    assert version.status is DocumentProcessingStatus.QUEUED
    assert version.attempt_count == 1
    assert version.processing_task_id is None
    assert version.error_code == DocumentErrorCode.SCANNER_UNAVAILABLE.value
    assert terminal_events == 0


def test_transient_scanner_failure_retries_then_reaches_ready(
    database_engine: Engine,
) -> None:
    storage = InMemoryDocumentStorage()
    persisted = create_queued_version(database_engine, storage)
    scanner = SequenceScanner(
        [DocumentScanResult.UNAVAILABLE, DocumentScanResult.CLEAN]
    )
    application = Celery("transient-ingestion-test", broker="memory://")
    service = ingestion_service(database_engine, storage, scanner=scanner)
    task_name = f"supportflow.documents.ingest.transient.{uuid4().hex}"
    task = register_document_ingestion_task(
        application,
        lambda: service,
        max_retries=1,
        soft_time_limit=60,
        hard_time_limit=75,
        task_name=task_name,
    )
    application.conf.task_always_eager = True

    result = task.apply(
        args=[str(persisted.version_id)],
        task_id="transient-delivery",
    )

    with Session(database_engine) as session:
        version = session.get(DocumentVersion, persisted.version_id)
        ready_event_count = session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == AuditAction.DOCUMENT_READY.value)
        )
    assert result.successful()
    assert scanner.calls == 2
    assert version is not None
    assert version.status is DocumentProcessingStatus.READY
    assert version.attempt_count == 2
    assert ready_event_count == 1


def test_permanent_corrupt_pdf_fails_once_with_atomic_audit(
    database_engine: Engine,
) -> None:
    storage = InMemoryDocumentStorage()
    persisted = create_queued_version(
        database_engine,
        storage,
        media_type=DocumentMediaType.PDF,
        content=b"%PDF-corrupt-private-canary",
        filename="private-corrupt-name.pdf",
    )

    outcome = ingestion_service(database_engine, storage).process(
        persisted.version_id,
        "permanent-delivery",
    )

    with Session(database_engine) as session:
        version = session.get(DocumentVersion, persisted.version_id)
        failed_events = list(
            session.scalars(
                select(AuditEvent).where(
                    AuditEvent.action == AuditAction.DOCUMENT_FAILED.value
                )
            )
        )
    assert outcome is IngestionOutcome.FAILED
    assert version is not None
    assert version.status is DocumentProcessingStatus.FAILED
    assert version.attempt_count == 1
    assert version.error_code == DocumentErrorCode.EXTRACTION_FAILED.value
    assert len(failed_events) == 1
    assert failed_events[0].event_metadata == {
        "version_number": 1,
        "error_code": DocumentErrorCode.EXTRACTION_FAILED.value,
        "attempt_count": 1,
    }


def test_storage_object_must_match_database_size_and_digest(
    database_engine: Engine,
) -> None:
    storage = InMemoryDocumentStorage()
    persisted = create_queued_version(
        database_engine,
        storage,
        content=b"original-private-content",
    )
    storage.objects[persisted.storage_key] = b"tampered-private-content"
    scanner = SequenceScanner([DocumentScanResult.CLEAN])

    outcome = ingestion_service(
        database_engine,
        storage,
        scanner=scanner,
    ).process(persisted.version_id, "tampered-delivery")

    with Session(database_engine) as session:
        version = session.get(DocumentVersion, persisted.version_id)
    assert outcome is IngestionOutcome.FAILED
    assert scanner.calls == 0
    assert version is not None
    assert version.status is DocumentProcessingStatus.FAILED
    assert version.error_code == DocumentErrorCode.EXTRACTION_FAILED.value
    assert version.extracted_text is None


def test_scanner_runs_before_deterministic_text_parse_failure(
    database_engine: Engine,
) -> None:
    storage = InMemoryDocumentStorage()
    persisted = create_queued_version(
        database_engine,
        storage,
        content=b"invalid-utf8-\xff",
    )
    scanner = SequenceScanner([DocumentScanResult.CLEAN])

    outcome = ingestion_service(
        database_engine,
        storage,
        scanner=scanner,
    ).process(persisted.version_id, "scan-before-parse")

    with Session(database_engine) as session:
        version = session.get(DocumentVersion, persisted.version_id)
    assert scanner.calls == 1
    assert outcome is IngestionOutcome.FAILED
    assert version is not None
    assert version.error_code == DocumentErrorCode.UNSUPPORTED_DOCUMENT.value


def test_infected_document_fails_without_parsing_or_retry(
    database_engine: Engine,
) -> None:
    storage = InMemoryDocumentStorage()
    persisted = create_queued_version(database_engine, storage)
    scanner = SequenceScanner([DocumentScanResult.INFECTED])

    outcome = ingestion_service(
        database_engine,
        storage,
        scanner=scanner,
    ).process(persisted.version_id, "infected-delivery")

    with Session(database_engine) as session:
        version = session.get(DocumentVersion, persisted.version_id)
    assert scanner.calls == 1
    assert outcome is IngestionOutcome.FAILED
    assert version is not None
    assert version.status is DocumentProcessingStatus.FAILED
    assert version.error_code == DocumentErrorCode.MALWARE_DETECTED.value
    assert version.extracted_text is None


def test_retry_exhaustion_via_celery_task_reaches_safe_failed_state(
    database_engine: Engine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="app.documents.tasks")
    storage = InMemoryDocumentStorage()
    persisted = create_queued_version(database_engine, storage)
    scanner = SequenceScanner(
        [DocumentScanResult.UNAVAILABLE, DocumentScanResult.UNAVAILABLE]
    )
    application = Celery("exhausted-ingestion-test", broker="memory://")
    service = RetryStateRecordingService(database_engine, storage, scanner)
    task_name = f"supportflow.documents.ingest.exhausted.{uuid4().hex}"
    task = register_document_ingestion_task(
        application,
        lambda: service,
        max_retries=1,
        soft_time_limit=60,
        hard_time_limit=75,
        task_name=task_name,
    )
    application.conf.task_always_eager = True

    result = task.apply(
        args=[str(persisted.version_id)],
        task_id="exhausted-delivery",
    )

    with Session(database_engine) as session:
        version = session.get(DocumentVersion, persisted.version_id)
        failed_event_count = session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == AuditAction.DOCUMENT_FAILED.value)
        )
    assert result.successful()
    assert result.result is None
    assert scanner.calls == 2
    assert version is not None
    assert version.status is DocumentProcessingStatus.FAILED
    assert version.error_code == DocumentErrorCode.RETRY_EXHAUSTED.value
    assert version.attempt_count == 2
    assert version.processing_task_id is None
    assert service.released_states == [
        (
            DocumentProcessingStatus.QUEUED,
            1,
            None,
            DocumentErrorCode.SCANNER_UNAVAILABLE.value,
        )
    ]
    assert caplog.text.count("document_ingestion_retry") == 1
    assert failed_event_count == 1


def invalid_ready_audit(
    service: AuditEventService,
    *,
    document_version_id: UUID,
    version_number: int,
    attempt_count: int,
    extracted_character_count: int,
) -> AuditEvent:
    event = ORIGINAL_RECORD_DOCUMENT_READY(
        service,
        document_version_id=document_version_id,
        version_number=version_number,
        attempt_count=attempt_count,
        extracted_character_count=extracted_character_count,
    )
    event.action = "invalid_without_required_dot"
    return event


def invalid_failed_audit(
    service: AuditEventService,
    *,
    document_version_id: UUID,
    version_number: int,
    error_code: DocumentErrorCode,
    attempt_count: int,
) -> AuditEvent:
    event = ORIGINAL_RECORD_DOCUMENT_FAILED(
        service,
        document_version_id=document_version_id,
        version_number=version_number,
        error_code=error_code,
        attempt_count=attempt_count,
    )
    event.action = "invalid_without_required_dot"
    return event


def test_terminal_state_rolls_back_when_audit_commit_fails(
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = InMemoryDocumentStorage()
    persisted = create_queued_version(database_engine, storage)
    service = ingestion_service(database_engine, storage)
    monkeypatch.setattr(
        AuditEventService,
        "record_document_ready",
        invalid_ready_audit,
    )

    with pytest.raises(RetryableIngestionError) as captured:
        service.process(persisted.version_id, "atomic-delivery")

    with Session(database_engine) as session:
        version = session.get(DocumentVersion, persisted.version_id)
        ready_event_count = session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == AuditAction.DOCUMENT_READY.value)
        )
    assert captured.value.error_code is DocumentErrorCode.DATABASE_UNAVAILABLE
    assert version is not None
    assert version.status is DocumentProcessingStatus.EXTRACTING
    assert version.extracted_text is None
    assert version.processing_task_id == "atomic-delivery"
    assert ready_event_count == 0


def test_failed_state_rolls_back_when_failed_audit_commit_fails(
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = InMemoryDocumentStorage()
    persisted = create_queued_version(
        database_engine,
        storage,
        media_type=DocumentMediaType.PDF,
        content=b"%PDF-corrupt-failed-audit",
    )
    service = ingestion_service(database_engine, storage)
    monkeypatch.setattr(
        AuditEventService,
        "record_document_failed",
        invalid_failed_audit,
    )

    with pytest.raises(RetryableIngestionError) as captured:
        service.process(persisted.version_id, "failed-atomic-delivery")

    with Session(database_engine) as session:
        version = session.get(DocumentVersion, persisted.version_id)
        failed_event_count = session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == AuditAction.DOCUMENT_FAILED.value)
        )
    assert captured.value.error_code is DocumentErrorCode.DATABASE_UNAVAILABLE
    assert version is not None
    assert version.status is DocumentProcessingStatus.EXTRACTING
    assert version.extracted_text is None
    assert version.error_code is None
    assert version.attempt_count == 1
    assert version.processing_task_id == "failed-atomic-delivery"
    assert failed_event_count == 0


def test_worker_logs_exclude_document_content_and_storage_details(
    database_engine: Engine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="app.documents.ingestion")
    storage = InMemoryDocumentStorage()
    content = b"private-worker-content-canary"
    filename = "private-worker-filename-canary.txt"
    persisted = create_queued_version(
        database_engine,
        storage,
        content=content,
        filename=filename,
    )

    ingestion_service(database_engine, storage).process(
        persisted.version_id,
        "safe-log-delivery",
    )

    assert "queued_to_extracting" in caplog.text
    assert "extracting_to_ready" in caplog.text
    assert "error_category=none" in caplog.text
    assert filename not in caplog.text
    assert content.decode() not in caplog.text
    assert persisted.storage_key not in caplog.text
    assert "content_sha256" not in caplog.text


def test_version_uuid_selects_only_its_database_owned_tenant(
    database_engine: Engine,
) -> None:
    storage = InMemoryDocumentStorage()
    tenant_a = create_queued_version(
        database_engine,
        storage,
        content=b"tenant-a-content",
    )
    tenant_b = create_queued_version(
        database_engine,
        storage,
        content=b"tenant-b-content",
    )

    ingestion_service(database_engine, storage).process(
        tenant_a.version_id,
        "tenant-a-delivery",
    )

    with Session(database_engine) as session:
        first = session.get(DocumentVersion, tenant_a.version_id)
        second = session.get(DocumentVersion, tenant_b.version_id)
    assert first is not None and second is not None
    assert first.organization_id == tenant_a.organization_id
    assert first.status is DocumentProcessingStatus.READY
    assert first.extracted_text == "tenant-a-content"
    assert second.organization_id == tenant_b.organization_id
    assert second.status is DocumentProcessingStatus.QUEUED
    assert second.extracted_text is None
