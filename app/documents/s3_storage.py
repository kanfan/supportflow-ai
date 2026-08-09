from __future__ import annotations

from collections.abc import Mapping
from typing import BinaryIO, Protocol, cast

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.documents.storage import validate_storage_key


class S3Client(Protocol):
    """Narrow synchronous S3 client surface required by the storage adapter."""

    def put_object(
        self,
        *,
        Bucket: str,
        Key: str,
        Body: BinaryIO,
    ) -> object: ...

    def get_object(self, *, Bucket: str, Key: str) -> Mapping[str, object]: ...

    def delete_object(self, *, Bucket: str, Key: str) -> object: ...

    def close(self) -> None: ...


class S3DocumentStorageError(RuntimeError):
    """Base class for stable errors raised by the S3 adapter."""


class S3ObjectNotFoundError(S3DocumentStorageError):
    """Raised when a database-referenced private object is missing."""


class S3StorageUnavailableError(S3DocumentStorageError):
    """Raised when S3 cannot complete a storage operation safely."""


def build_s3_client(
    *,
    region_name: str,
    endpoint_url: str | None,
    connect_timeout_seconds: int,
    read_timeout_seconds: int,
    total_max_attempts: int,
) -> S3Client:
    """Build an S3 client that relies on the standard ECS task-role chain."""

    client = boto3.client(
        "s3",
        region_name=region_name,
        endpoint_url=endpoint_url,
        config=Config(
            connect_timeout=connect_timeout_seconds,
            read_timeout=read_timeout_seconds,
            retries={
                "mode": "standard",
                "total_max_attempts": total_max_attempts,
            },
        ),
    )
    return cast(S3Client, client)


class S3DocumentStorage:
    """Private S3 implementation of the shared document-storage port."""

    def __init__(self, client: S3Client, bucket: str) -> None:
        normalized_bucket = bucket.strip()
        if not normalized_bucket:
            raise ValueError("S3 bucket must not be empty")
        self._client = client
        self._bucket = normalized_bucket

    @staticmethod
    def _validated_key(key: str) -> str:
        return validate_storage_key(key).as_posix()

    def put(self, key: str, source: BinaryIO) -> None:
        validated_key = self._validated_key(key)
        source.seek(0)
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=validated_key,
                Body=source,
            )
        except (BotoCoreError, ClientError):
            raise S3StorageUnavailableError(
                "Private document storage is unavailable"
            ) from None

    def open(self, key: str) -> BinaryIO:
        validated_key = self._validated_key(key)
        try:
            response = self._client.get_object(
                Bucket=self._bucket,
                Key=validated_key,
            )
        except ClientError as exc:
            error_code = str(exc.response.get("Error", {}).get("Code", ""))
            if error_code in {"404", "NoSuchKey", "NotFound"}:
                raise S3ObjectNotFoundError(
                    "Stored document object was not found"
                ) from None
            raise S3StorageUnavailableError(
                "Private document storage is unavailable"
            ) from None
        except BotoCoreError:
            raise S3StorageUnavailableError(
                "Private document storage is unavailable"
            ) from None

        body = response.get("Body")
        if body is None or not hasattr(body, "read") or not hasattr(body, "close"):
            raise S3StorageUnavailableError(
                "Private document storage returned an invalid response"
            )
        return cast(BinaryIO, body)

    def delete(self, key: str) -> None:
        validated_key = self._validated_key(key)
        try:
            self._client.delete_object(
                Bucket=self._bucket,
                Key=validated_key,
            )
        except (BotoCoreError, ClientError):
            raise S3StorageUnavailableError(
                "Private document storage is unavailable"
            ) from None

    def close(self) -> None:
        self._client.close()
