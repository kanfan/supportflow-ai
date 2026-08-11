from __future__ import annotations

import logging
import random
from collections.abc import Callable
from typing import Any, cast
from uuid import UUID

from celery import Celery, Task

from app.documents.ingestion import (
    DocumentIngestionService,
    INGEST_DOCUMENT_VERSION_TASK,
    RetryableIngestionError,
)
from app.documents.models import DocumentErrorCode


logger = logging.getLogger(__name__)


class SafeRetrySignal(RuntimeError):
    """Sanitized Celery retry cause containing only an allowlisted code."""


class DocumentIngestionTask(Task):
    abstract = True
    acks_late = True
    reject_on_worker_lost = True
    ignore_result = True
    store_errors_even_if_ignored = False
    autoretry_for: tuple[type[Exception], ...] = ()


def retry_countdown(retry_number: int, *, jitter: float | None = None) -> int:
    """Return capped exponential backoff with bounded positive jitter."""

    base = min(60, 2 ** max(0, retry_number))
    fraction = random.random() if jitter is None else max(0.0, min(1.0, jitter))
    return min(60, base + round(base * fraction))


def register_document_ingestion_task(
    application: Celery,
    service_provider: Callable[[], DocumentIngestionService],
    *,
    max_retries: int,
    soft_time_limit: int,
    hard_time_limit: int,
    task_name: str = INGEST_DOCUMENT_VERSION_TASK,
) -> Task:
    @application.task(
        bind=True,
        base=DocumentIngestionTask,
        name=task_name,
        max_retries=max_retries,
        soft_time_limit=soft_time_limit,
        time_limit=hard_time_limit,
    )
    def ingest_document_version(
        task: Task,
        document_version_id: str,
    ) -> None:
        try:
            version_id = UUID(document_version_id)
        except (TypeError, ValueError, AttributeError):
            logger.warning(
                "document_ingestion_invalid_payload task_name=%s task_id=%s "
                "error_category=invalid_identifier",
                task.name,
                task.request.id or "unknown",
            )
            return

        task_id = str(task.request.id or "unknown")
        service = service_provider()
        try:
            service.process(version_id, task_id)
        except RetryableIngestionError as exc:
            retries = int(task.request.retries)
            if retries >= max_retries:
                service.mark_retry_exhausted(version_id, task_id)
                return

            retry_code = exc.error_code
            try:
                service.prepare_retry(version_id, task_id, retry_code)
            except RetryableIngestionError:
                retry_code = DocumentErrorCode.DATABASE_UNAVAILABLE

            countdown = retry_countdown(retries)
            logger.warning(
                "document_ingestion_retry task_name=%s task_id=%s version_id=%s "
                "attempt=%s error_category=%s retry_countdown=%s",
                task.name,
                task_id,
                version_id,
                retries + 1,
                retry_code.value,
                countdown,
            )
            raise task.retry(
                exc=SafeRetrySignal(retry_code.value),
                countdown=countdown,
            )

    return cast(Task, ingest_document_version)


def ingestion_task_options(task: Task) -> dict[str, Any]:
    """Expose reliability settings for focused contract tests and diagnostics."""

    return {
        "acks_late": task.acks_late,
        "reject_on_worker_lost": task.reject_on_worker_lost,
        "ignore_result": task.ignore_result,
        "max_retries": task.max_retries,
        "soft_time_limit": task.soft_time_limit,
        "time_limit": task.time_limit,
    }
