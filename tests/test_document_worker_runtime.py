from collections.abc import Callable
from io import BytesIO
from typing import cast

import pytest
from pydantic import SecretStr
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.config import Settings
from app.documents.ports import DocumentStorage
from app.documents.s3_storage import S3Client, S3DocumentStorage
from app.documents.worker_runtime import DocumentWorkerRuntime
from app.worker import create_celery


class RecordingS3Client:
    def __init__(self) -> None:
        self.close_calls = 0

    def put_object(self, **_kwargs: object) -> object:
        return {}

    def get_object(self, **_kwargs: object) -> dict[str, object]:
        return {"Body": BytesIO()}

    def delete_object(self, **_kwargs: object) -> object:
        return {}

    def close(self) -> None:
        self.close_calls += 1


class RecordingEngine:
    def __init__(self) -> None:
        self.dispose_calls = 0

    def dispose(self) -> None:
        self.dispose_calls += 1


def s3_settings() -> Settings:
    return Settings(
        environment="test",
        document_storage_mode="s3",
        document_s3_bucket="supportflow-test-documents",
        document_s3_region="eu-central-1",
        celery_broker_url=SecretStr("memory://"),
        celery_result_backend_url=SecretStr("cache+memory://"),
    )


def test_creating_celery_in_parent_does_not_create_worker_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver_calls = 0

    def fail_if_resolved(
        _settings: Settings,
        _configured_storage: DocumentStorage | None,
    ) -> DocumentStorage:
        nonlocal resolver_calls
        resolver_calls += 1
        raise AssertionError("parent process must not create worker storage")

    monkeypatch.setattr(
        "app.documents.worker_runtime.resolve_document_storage",
        fail_if_resolved,
    )

    create_celery(s3_settings())

    assert resolver_calls == 0


def test_each_worker_process_replaces_inherited_resources_and_closes_its_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clients: list[RecordingS3Client] = []
    engines: list[RecordingEngine] = []
    current_pid = 1001

    def fake_getpid() -> int:
        return current_pid

    def build_storage(
        _settings: Settings,
        _configured_storage: DocumentStorage | None,
    ) -> DocumentStorage:
        client = RecordingS3Client()
        clients.append(client)
        return S3DocumentStorage(cast(S3Client, client), "worker-private-documents")

    def build_worker_engine(
        _database_url: str,
        *,
        connect_timeout_seconds: int,
    ) -> Engine:
        assert connect_timeout_seconds == 2
        engine = RecordingEngine()
        engines.append(engine)
        return cast(Engine, engine)

    def build_worker_session_factory(
        _engine: Engine,
    ) -> Callable[[], Session]:
        def create_session() -> Session:
            return cast(Session, object())

        return create_session

    monkeypatch.setattr(
        "app.documents.worker_runtime.os.getpid",
        fake_getpid,
    )
    monkeypatch.setattr(
        "app.documents.worker_runtime.resolve_document_storage",
        build_storage,
    )
    monkeypatch.setattr(
        "app.documents.worker_runtime.build_engine",
        build_worker_engine,
    )
    monkeypatch.setattr(
        "app.documents.worker_runtime.build_session_factory",
        build_worker_session_factory,
    )
    runtime = DocumentWorkerRuntime(s3_settings())

    first_service = runtime.get_service()
    runtime.initialize_child()

    assert runtime.get_service() is first_service
    assert len(clients) == 1
    assert len(engines) == 1

    current_pid = 1002
    second_service = runtime.get_service()

    assert second_service is not first_service
    assert len(clients) == 2
    assert len(engines) == 2
    assert clients[0].close_calls == 1
    assert engines[0].dispose_calls == 1

    runtime.shutdown_child()
    runtime.shutdown_child()

    assert clients[1].close_calls == 1
    assert engines[1].dispose_calls == 1
