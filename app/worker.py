from collections.abc import Callable

from celery import Celery
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.documents.composition import resolve_document_safety_scanner
from app.documents.extraction import DocumentExtractorRegistry, ExtractionLimits
from app.documents.ingestion import DocumentIngestionService
from app.documents.ports import DocumentSafetyScanner, DocumentStorage
from app.documents.storage import LocalDocumentStorage
from app.documents.tasks import register_document_ingestion_task
from app.infrastructure.database import build_engine, build_session_factory


HEALTH_TASK_NAME = "supportflow.health.ping"


def health_ping() -> dict[str, str]:
    return {"status": "ok"}


def create_celery_client(settings: Settings | None = None) -> Celery:
    """Create a publish-only client without composing worker dependencies."""

    resolved_settings = settings or get_settings()
    application = Celery(
        "supportflow",
        broker=resolved_settings.celery_broker_url,
        backend=resolved_settings.celery_result_backend_url,
    )
    application.conf.update(
        accept_content=["json"],
        broker_connection_retry_on_startup=True,
        result_serializer="json",
        task_serializer="json",
        timezone="UTC",
        worker_prefetch_multiplier=1,
        broker_transport_options={
            "visibility_timeout": (resolved_settings.celery_visibility_timeout_seconds)
        },
        result_backend_transport_options={
            "visibility_timeout": (resolved_settings.celery_visibility_timeout_seconds)
        },
        visibility_timeout=resolved_settings.celery_visibility_timeout_seconds,
    )
    return application


def create_celery(
    settings: Settings | None = None,
    *,
    document_safety_scanner: DocumentSafetyScanner | None = None,
    document_storage: DocumentStorage | None = None,
    session_factory: Callable[[], Session] | None = None,
    document_extractors: DocumentExtractorRegistry | None = None,
) -> Celery:
    resolved_settings = settings or get_settings()
    resolved_scanner = resolve_document_safety_scanner(
        resolved_settings,
        document_safety_scanner,
    )
    resolved_storage = document_storage or LocalDocumentStorage(
        resolved_settings.document_storage_root
    )
    if session_factory is None:
        engine = build_engine(resolved_settings.database_url)
        resolved_session_factory: Callable[[], Session] = build_session_factory(engine)
    else:
        resolved_session_factory = session_factory
    ingestion_service = DocumentIngestionService(
        resolved_session_factory,
        storage=resolved_storage,
        scanner=resolved_scanner,
        extractors=document_extractors or DocumentExtractorRegistry(),
        limits=ExtractionLimits(
            max_pdf_pages=resolved_settings.document_max_pdf_pages,
            max_characters=resolved_settings.document_max_extracted_characters,
        ),
    )
    application = create_celery_client(resolved_settings)
    application.task(name=HEALTH_TASK_NAME)(health_ping)
    register_document_ingestion_task(
        application,
        ingestion_service,
        max_retries=resolved_settings.document_ingestion_max_retries,
        soft_time_limit=(resolved_settings.document_ingestion_soft_time_limit_seconds),
        hard_time_limit=(resolved_settings.document_ingestion_hard_time_limit_seconds),
    )
    return application


celery_app = create_celery()
