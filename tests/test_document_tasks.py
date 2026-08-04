from dataclasses import dataclass, field
from typing import cast
from uuid import UUID, uuid4

from celery import Celery

from app.documents.ingestion import (
    DocumentIngestionService,
    INGEST_DOCUMENT_VERSION_TASK,
)
from app.documents.tasks import (
    ingestion_task_options,
    register_document_ingestion_task,
    retry_countdown,
)


@dataclass
class RecordingIngestionService:
    processed: list[tuple[UUID, str]] = field(default_factory=list)

    def process(self, version_id: UUID, task_id: str) -> None:
        self.processed.append((version_id, task_id))


def test_ingestion_task_policy_is_late_acknowledged_bounded_and_resultless() -> None:
    application = Celery("document-task-contract", broker="memory://")
    service = RecordingIngestionService()
    task = register_document_ingestion_task(
        application,
        cast(DocumentIngestionService, service),
        max_retries=3,
        soft_time_limit=60,
        hard_time_limit=75,
        task_name=f"{INGEST_DOCUMENT_VERSION_TASK}.policy",
    )

    assert task.name == f"{INGEST_DOCUMENT_VERSION_TASK}.policy"
    assert ingestion_task_options(task) == {
        "acks_late": True,
        "reject_on_worker_lost": True,
        "ignore_result": True,
        "max_retries": 3,
        "soft_time_limit": 60,
        "time_limit": 75,
    }


def test_ingestion_task_accepts_only_one_version_uuid() -> None:
    application = Celery("document-task-payload", broker="memory://")
    application.conf.task_always_eager = True
    service = RecordingIngestionService()
    task = register_document_ingestion_task(
        application,
        cast(DocumentIngestionService, service),
        max_retries=3,
        soft_time_limit=60,
        hard_time_limit=75,
        task_name=f"{INGEST_DOCUMENT_VERSION_TASK}.payload",
    )
    version_id = uuid4()

    task.apply(args=[str(version_id)], task_id="delivery-one")
    invalid = task.apply(args=["not-a-uuid"], task_id="delivery-invalid")

    assert service.processed == [(version_id, "delivery-one")]
    assert invalid.successful()
    assert invalid.result is None


def test_retry_backoff_is_exponential_jittered_and_capped() -> None:
    assert retry_countdown(0, jitter=0) == 1
    assert retry_countdown(0, jitter=1) == 2
    assert retry_countdown(1, jitter=0) == 2
    assert retry_countdown(2, jitter=0.5) == 6
    assert retry_countdown(10, jitter=1) == 60
