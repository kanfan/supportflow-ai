from __future__ import annotations

from io import BytesIO
import os
from pathlib import Path, PurePosixPath
import shutil
from typing import BinaryIO
from uuid import uuid4


class InvalidStorageKeyError(ValueError):
    """Raised when an object key could escape the configured private root."""


def validate_storage_key(key: str) -> PurePosixPath:
    path = PurePosixPath(key)
    if (
        not key
        or path.is_absolute()
        or "\\" in key
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise InvalidStorageKeyError("Invalid private storage key")
    return path


class LocalDocumentStorage:
    """Atomic private-filesystem adapter for local development."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, key: str) -> Path:
        relative = validate_storage_key(key)
        target = self._root.joinpath(*relative.parts).resolve()
        if not target.is_relative_to(self._root):
            raise InvalidStorageKeyError("Invalid private storage key")
        return target

    def put(self, key: str, source: BinaryIO) -> None:
        target = self._path_for(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            source.seek(0)
            with temporary.open("xb") as destination:
                shutil.copyfileobj(source, destination)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def open(self, key: str) -> BinaryIO:
        return self._path_for(key).open("rb")

    def delete(self, key: str) -> None:
        self._path_for(key).unlink(missing_ok=True)


class InMemoryDocumentStorage:
    """Deterministic storage adapter for unit and integration tests."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, key: str, source: BinaryIO) -> None:
        validate_storage_key(key)
        source.seek(0)
        self.objects[key] = source.read()

    def open(self, key: str) -> BinaryIO:
        validate_storage_key(key)
        return BytesIO(self.objects[key])

    def delete(self, key: str) -> None:
        validate_storage_key(key)
        self.objects.pop(key, None)
