from io import BytesIO

import pytest

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
