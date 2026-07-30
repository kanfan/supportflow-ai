from typing import Any, cast
from uuid import uuid4

from celery import Celery

from app.documents.dispatch import (
    INGEST_DOCUMENT_VERSION_TASK,
    CeleryDocumentTaskDispatcher,
)


class RecordingCelery:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str], dict[str, Any]]] = []

    def send_task(
        self,
        name: str,
        args: list[str],
        **options: Any,
    ) -> None:
        self.calls.append((name, args, options))


def test_dispatcher_publishes_only_the_version_identifier() -> None:
    celery = RecordingCelery()
    dispatcher = CeleryDocumentTaskDispatcher(cast(Celery, celery))
    version_id = uuid4()

    dispatcher.dispatch(version_id)

    assert celery.calls == [
        (
            INGEST_DOCUMENT_VERSION_TASK,
            [str(version_id)],
            {"ignore_result": True},
        )
    ]
