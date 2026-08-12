from __future__ import annotations

import json
import os
from time import monotonic, sleep
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import UUID, uuid4

from redis import Redis
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.models import AuditAction, AuditEvent
from app.config import get_settings
from app.documents.ingestion import INGEST_DOCUMENT_VERSION_TASK
from app.documents.models import DocumentProcessingStatus, DocumentVersion
from app.infrastructure.database import build_engine
from app.worker import create_celery_client


API_BASE_URL = os.getenv("SUPPORTFLOW_SMOKE_API_URL", "http://api:8000")
TIMEOUT_SECONDS = 30


def request_json(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    content_type: str = "application/json",
) -> dict[str, Any]:
    request_headers = dict(headers or {})
    if payload is not None:
        body = json.dumps(payload).encode()
    if body is not None:
        request_headers["Content-Type"] = content_type
    request = Request(
        f"{API_BASE_URL}{path}",
        data=body,
        headers=request_headers,
        method=method,
    )
    try:
        with urlopen(request, timeout=10) as response:  # noqa: S310
            return json.loads(response.read())
    except HTTPError as exc:
        raise RuntimeError(
            f"Document ingestion smoke request failed with HTTP {exc.code}"
        ) from None


def multipart_file(filename: str, content: bytes) -> tuple[bytes, str]:
    boundary = f"supportflow-smoke-{uuid4().hex}"
    body = (
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; '
            f'filename="{filename}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
        + content
        + f"\r\n--{boundary}--\r\n".encode()
    )
    return body, f"multipart/form-data; boundary={boundary}"


def wait_for_terminal(
    status_path: str,
    headers: dict[str, str],
) -> dict[str, Any]:
    deadline = monotonic() + TIMEOUT_SECONDS
    while monotonic() < deadline:
        status = request_json("GET", status_path, headers=headers)
        if status["version"]["status"] in {"ready", "failed"}:
            return status
        sleep(0.25)
    raise RuntimeError("Document ingestion smoke timed out")


def wait_for_queue_empty() -> None:
    settings = get_settings()
    queue = Redis.from_url(settings.celery_broker_url.get_secret_value())
    deadline = monotonic() + TIMEOUT_SECONDS
    while monotonic() < deadline:
        if queue.llen("celery") == 0:
            sleep(0.25)
            return
        sleep(0.1)
    raise RuntimeError("Duplicate ingestion deliveries did not leave the queue")


def verify_duplicate_noop(document_id: UUID, version_id: UUID) -> None:
    settings = get_settings()
    publisher = create_celery_client(settings)
    for _ in range(2):
        publisher.send_task(
            INGEST_DOCUMENT_VERSION_TASK,
            args=[str(version_id)],
            ignore_result=True,
        )
    wait_for_queue_empty()

    engine = build_engine(settings.database_url)
    try:
        with Session(engine) as session:
            version = session.get(DocumentVersion, version_id)
            version_count = session.scalar(
                select(func.count())
                .select_from(DocumentVersion)
                .where(DocumentVersion.document_id == document_id)
            )
            ready_event_count = session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.resource_id == version_id,
                    AuditEvent.action == AuditAction.DOCUMENT_READY.value,
                )
            )
        if version is None or version.status is not DocumentProcessingStatus.READY:
            raise RuntimeError("Duplicate delivery changed the terminal state")
        if version.attempt_count != 1 or version_count != 1 or ready_event_count != 1:
            raise RuntimeError("Duplicate delivery created an ingestion side effect")
    finally:
        engine.dispose()


def main() -> None:
    suffix = uuid4().hex
    password = f"Smoke-{uuid4().hex}-safe"
    registration = request_json(
        "POST",
        "/api/v1/auth/register",
        payload={
            "email": f"document-smoke-{suffix}@example.com",
            "password": password,
            "organization_name": f"Document Smoke {suffix}",
            "organization_slug": f"document-smoke-{suffix}",
        },
    )
    login = request_json(
        "POST",
        "/api/v1/auth/login",
        payload={
            "email": f"document-smoke-{suffix}@example.com",
            "password": password,
        },
    )
    headers = {
        "Authorization": f"Bearer {login['access_token']}",
        "X-Organization-ID": registration["organization"]["id"],
    }
    upload_body, upload_type = multipart_file(
        "smoke-guide.md",
        b"# SupportFlow smoke guide\nWorker extraction is ready.",
    )
    created = request_json(
        "POST",
        "/api/v1/documents",
        headers=headers,
        body=upload_body,
        content_type=upload_type,
    )
    status_path = f"/api/v1/documents/{created['id']}"
    terminal = wait_for_terminal(status_path, headers)
    if terminal["version"]["status"] != "ready":
        raise RuntimeError("Document ingestion smoke reached a failed state")
    if terminal["version"]["error_code"] is not None:
        raise RuntimeError("Ready document unexpectedly exposed an error code")
    verify_duplicate_noop(
        UUID(created["id"]),
        UUID(created["version"]["id"]),
    )

    corrupt_body, corrupt_type = multipart_file(
        "corrupt-smoke.pdf",
        b"%PDF-1.4\ncorrupt-smoke-canary",
    )
    corrupt = request_json(
        "POST",
        "/api/v1/documents",
        headers=headers,
        body=corrupt_body,
        content_type=corrupt_type,
    )
    corrupt_terminal = wait_for_terminal(
        f"/api/v1/documents/{corrupt['id']}",
        headers,
    )
    if corrupt_terminal["version"]["status"] != "failed":
        raise RuntimeError("Corrupt document did not reach a safe failed state")
    if corrupt_terminal["version"]["error_code"] != "extraction_failed":
        raise RuntimeError("Corrupt document exposed an unexpected error category")
    if "corrupt-smoke-canary" in json.dumps(corrupt_terminal):
        raise RuntimeError("Document content leaked into the status response")
    print(
        "Document ingestion smoke passed: "
        f"document_id={created['id']} version_id={created['version']['id']} "
        "status=ready duplicate=noop corrupt=failed"
    )


if __name__ == "__main__":
    main()
