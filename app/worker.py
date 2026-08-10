from collections.abc import Callable

from celery import Celery
from celery.signals import worker_process_init, worker_process_shutdown
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.documents.extraction import DocumentExtractorRegistry
from app.documents.ports import DocumentSafetyScanner, DocumentStorage
from app.documents.tasks import register_document_ingestion_task
from app.documents.worker_runtime import DocumentWorkerRuntime


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
    worker_runtime = DocumentWorkerRuntime(
        resolved_settings,
        document_safety_scanner=document_safety_scanner,
        document_storage=document_storage,
        session_factory=session_factory,
        document_extractors=document_extractors,
    )
    application = create_celery_client(resolved_settings)
    application.task(name=HEALTH_TASK_NAME)(health_ping)
    register_document_ingestion_task(
        application,
        worker_runtime.get_service,
        max_retries=resolved_settings.document_ingestion_max_retries,
        soft_time_limit=(resolved_settings.document_ingestion_soft_time_limit_seconds),
        hard_time_limit=(resolved_settings.document_ingestion_hard_time_limit_seconds),
    )
    worker_process_init.connect(worker_runtime.initialize_child, weak=False)
    worker_process_shutdown.connect(worker_runtime.shutdown_child, weak=False)
    return application


celery_app = create_celery()
