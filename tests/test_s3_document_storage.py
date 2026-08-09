from collections.abc import Callable
from contextlib import closing
from io import BytesIO
from typing import cast

import boto3
from botocore.client import BaseClient
from botocore.response import StreamingBody
from botocore.stub import ANY, Stubber
import pytest

from app.documents.s3_storage import (
    S3Client,
    S3DocumentStorage,
    S3ObjectNotFoundError,
    S3StorageUnavailableError,
    build_s3_client,
)
from app.documents.storage import InvalidStorageKeyError


BUCKET = "supportflow-test-documents"
KEY = "organizations/org/documents/doc/versions/version"


def stubbed_storage() -> tuple[BaseClient, S3DocumentStorage]:
    client = cast(
        BaseClient,
        boto3.client(
            "s3",
            region_name="eu-central-1",
            aws_access_key_id="test-only",
            aws_secret_access_key="test-only",
        ),
    )
    return client, S3DocumentStorage(cast(S3Client, client), BUCKET)


def test_s3_storage_put_open_and_delete_follow_the_private_object_contract() -> None:
    client, storage = stubbed_storage()
    source = BytesIO(b"private document")
    source.seek(4)
    stored_body = StreamingBody(BytesIO(b"private document"), 16)

    with Stubber(client) as stubber:
        stubber.add_response(
            "put_object",
            {},
            {"Bucket": BUCKET, "Key": KEY, "Body": ANY},
        )
        stubber.add_response(
            "get_object",
            {"Body": stored_body, "ContentLength": 16},
            {"Bucket": BUCKET, "Key": KEY},
        )
        stubber.add_response(
            "delete_object",
            {},
            {"Bucket": BUCKET, "Key": KEY},
        )

        storage.put(KEY, source)
        assert source.tell() == 0
        with closing(storage.open(KEY)) as opened:
            assert opened.read() == b"private document"
        storage.delete(KEY)
        stubber.assert_no_pending_responses()

    client.close()


def test_s3_storage_maps_missing_objects_to_a_stable_safe_error() -> None:
    client, storage = stubbed_storage()
    sensitive_provider_message = (
        "missing s3://supportflow-secret-bucket/organizations/private-object"
    )

    with Stubber(client) as stubber:
        stubber.add_client_error(
            "get_object",
            service_error_code="NoSuchKey",
            service_message=sensitive_provider_message,
            http_status_code=404,
            expected_params={"Bucket": BUCKET, "Key": KEY},
        )

        with pytest.raises(S3ObjectNotFoundError) as captured:
            storage.open(KEY)

    assert sensitive_provider_message not in str(captured.value)
    assert BUCKET not in str(captured.value)
    client.close()


@pytest.mark.parametrize(
    ("client_method", "invoke", "expected_params"),
    [
        (
            "put_object",
            lambda storage: storage.put(KEY, BytesIO(b"private")),
            {"Bucket": BUCKET, "Key": KEY, "Body": ANY},
        ),
        (
            "get_object",
            lambda storage: storage.open(KEY),
            {"Bucket": BUCKET, "Key": KEY},
        ),
        (
            "delete_object",
            lambda storage: storage.delete(KEY),
            {"Bucket": BUCKET, "Key": KEY},
        ),
    ],
)
def test_s3_storage_maps_provider_failures_without_leaking_details(
    client_method: str,
    invoke: Callable[[S3DocumentStorage], object],
    expected_params: dict[str, object],
) -> None:
    client, storage = stubbed_storage()
    sensitive_provider_message = (
        "credential=secret s3://supportflow-secret-bucket/private-object"
    )

    with Stubber(client) as stubber:
        stubber.add_client_error(
            client_method,
            service_error_code="InternalError",
            service_message=sensitive_provider_message,
            http_status_code=500,
            expected_params=expected_params,
        )

        with pytest.raises(S3StorageUnavailableError) as captured:
            invoke(storage)

    assert sensitive_provider_message not in str(captured.value)
    assert "credential" not in str(captured.value)
    assert BUCKET not in str(captured.value)
    client.close()


@pytest.mark.parametrize(
    "key",
    ["", "/absolute", "../escape", "organizations/../escape", r"bad\key"],
)
def test_s3_storage_rejects_invalid_keys_before_an_sdk_call(key: str) -> None:
    client, storage = stubbed_storage()

    with Stubber(client) as stubber:
        with pytest.raises(InvalidStorageKeyError):
            storage.put(key, BytesIO(b"unsafe"))
        stubber.assert_no_pending_responses()

    client.close()


def test_s3_client_uses_bounded_retries_without_explicit_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    sentinel = cast(S3Client, object())

    def fake_client(service_name: str, **kwargs: object) -> object:
        captured["service_name"] = service_name
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr("app.documents.s3_storage.boto3.client", fake_client)

    client = build_s3_client(
        region_name="eu-central-1",
        endpoint_url="https://s3.internal.example",
        connect_timeout_seconds=2,
        read_timeout_seconds=30,
        total_max_attempts=3,
    )

    assert client is sentinel
    assert captured["service_name"] == "s3"
    assert captured["region_name"] == "eu-central-1"
    assert captured["endpoint_url"] == "https://s3.internal.example"
    config = cast(object, captured["config"])
    assert getattr(config, "connect_timeout") == 2
    assert getattr(config, "read_timeout") == 30
    assert getattr(config, "retries") == {
        "mode": "standard",
        "total_max_attempts": 3,
    }
    assert "aws_access_key_id" not in captured
    assert "aws_secret_access_key" not in captured
