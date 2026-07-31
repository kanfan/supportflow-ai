from uuid import UUID

from celery import Celery


INGEST_DOCUMENT_VERSION_TASK = "supportflow.documents.ingest"


class CeleryDocumentTaskDispatcher:
    """Publish the ID-only task without owning its worker implementation."""

    def __init__(self, celery_application: Celery) -> None:
        self._celery_application = celery_application

    def dispatch(self, document_version_id: UUID) -> None:
        self._celery_application.send_task(
            INGEST_DOCUMENT_VERSION_TASK,
            args=[str(document_version_id)],
            ignore_result=True,
        )
