from io import BytesIO

import pytest

from app.config import Settings
from app.documents.composition import close_document_storage, resolve_document_storage
from app.documents.s3_storage import S3Client, S3DocumentStorage
from app.documents.storage import (
    InMemoryDocumentStorage,
    InvalidStorageKeyError,
    LocalDocumentStorage,
)


def test_local_storage_put_open_and_delete_are_private_and_repeatable(
    tmp_path,
) -> None:
    root = tmp_path / "private-documents"
    storage = LocalDocumentStorage(root)
    key = "organizations/org/documents/doc/versions/version"

    storage.put(key, BytesIO(b"first"))
    with storage.open(key) as stored:
        assert stored.read() == b"first"

    storage.put(key, BytesIO(b"replacement"))
    with storage.open(key) as stored:
        assert stored.read() == b"replacement"

    storage.delete(key)
    assert not root.joinpath(*key.split("/")).exists()


@pytest.mark.parametrize(
    "key",
    [
        "",
        "/absolute/object",
        "../escape",
        "organizations/../escape",
        r"organizations\tenant\document",
    ],
)
def test_storage_rejects_keys_that_can_escape_private_namespace(
    tmp_path,
    key: str,
) -> None:
    storage = LocalDocumentStorage(tmp_path / "private")

    with pytest.raises(InvalidStorageKeyError):
        storage.put(key, BytesIO(b"unsafe"))


def test_in_memory_storage_is_a_deterministic_test_adapter() -> None:
    storage = InMemoryDocumentStorage()
    key = "organizations/a/documents/b/versions/c"

    storage.put(key, BytesIO(b"content"))
    with storage.open(key) as stored:
        assert stored.read() == b"content"
    storage.delete(key)

    assert storage.objects == {}


def test_storage_resolver_preserves_private_local_storage_by_default(
    tmp_path,
) -> None:
    storage = resolve_document_storage(
        Settings(environment="test", document_storage_root=tmp_path / "private"),
        None,
    )

    assert isinstance(storage, LocalDocumentStorage)


def test_storage_resolver_builds_s3_from_non_secret_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel_client = object()
    captured: dict[str, object] = {}

    def fake_build_s3_client(**kwargs: object) -> S3Client:
        captured.update(kwargs)
        return sentinel_client  # type: ignore[return-value]

    monkeypatch.setattr(
        "app.documents.composition.build_s3_client",
        fake_build_s3_client,
    )
    settings = Settings(
        environment="test",
        document_storage_mode="s3",
        document_s3_bucket="supportflow-test-documents",
        document_s3_region="eu-central-1",
        document_s3_endpoint_url="https://s3.internal.example",
    )

    storage = resolve_document_storage(settings, None)

    assert isinstance(storage, S3DocumentStorage)
    assert captured == {
        "region_name": "eu-central-1",
        "endpoint_url": "https://s3.internal.example",
        "connect_timeout_seconds": 2,
        "read_timeout_seconds": 30,
        "total_max_attempts": 3,
    }


def test_storage_resolver_preserves_an_injected_test_adapter() -> None:
    configured = InMemoryDocumentStorage()

    assert (
        resolve_document_storage(Settings(environment="test"), configured) is configured
    )


def test_storage_resolver_normalizes_s3_configuration_and_closes_its_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ClosingClient:
        def __init__(self) -> None:
            self.closed = False

        def put_object(self, **_kwargs: object) -> object:
            return {}

        def get_object(self, **_kwargs: object) -> dict[str, object]:
            return {"Body": BytesIO()}

        def delete_object(self, **_kwargs: object) -> object:
            return {}

        def close(self) -> None:
            self.closed = True

    client = ClosingClient()
    captured: dict[str, object] = {}

    def fake_build_s3_client(**kwargs: object) -> S3Client:
        captured.update(kwargs)
        return client  # type: ignore[return-value]

    monkeypatch.setattr(
        "app.documents.composition.build_s3_client",
        fake_build_s3_client,
    )
    storage = resolve_document_storage(
        Settings(
            environment="test",
            document_storage_mode="s3",
            document_s3_bucket="  supportflow-test-documents  ",
            document_s3_region="  eu-central-1  ",
            document_s3_endpoint_url="   ",
        ),
        None,
    )

    assert captured["region_name"] == "eu-central-1"
    assert captured["endpoint_url"] is None
    close_document_storage(storage)
    assert client.closed is True
