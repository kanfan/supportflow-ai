from __future__ import annotations

import json
from pathlib import Path
import sys
from time import monotonic, sleep
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.models import AuditAction, AuditEvent
from app.config import get_settings
from app.documents.models import DocumentProcessingStatus, DocumentVersion
from app.infrastructure.database import build_engine
from scripts.smoke_document_ingestion import multipart_file, request_json


TIMEOUT_SECONDS = 60
EXPECTED_TEXT = "claimed-worker-loss-content"
GATE_FILENAME = ".worker-redelivery-release"
EVIDENCE_FILENAME = ".worker-redelivery-evidence.json"


def artifact_path(filename: str) -> Path:
    root = get_settings().document_storage_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root / filename


def register_and_upload() -> tuple[UUID, UUID]:
    suffix = uuid4().hex
    password = f"Redelivery-{uuid4().hex}-safe"
    registration = request_json(
        "POST",
        "/api/v1/auth/register",
        payload={
            "email": f"redelivery-smoke-{suffix}@example.com",
            "password": password,
            "organization_name": f"Redelivery Smoke {suffix}",
            "organization_slug": f"redelivery-smoke-{suffix}",
        },
    )
    login = request_json(
        "POST",
        "/api/v1/auth/login",
        payload={
            "email": f"redelivery-smoke-{suffix}@example.com",
            "password": password,
        },
    )
    headers = {
        "Authorization": f"Bearer {login['access_token']}",
        "X-Organization-ID": registration["organization"]["id"],
    }
    upload_body, upload_type = multipart_file(
        "worker-loss-guide.txt",
        EXPECTED_TEXT.encode(),
    )
    created = request_json(
        "POST",
        "/api/v1/documents",
        headers=headers,
        body=upload_body,
        content_type=upload_type,
    )
    return UUID(created["id"]), UUID(created["version"]["id"])


def prepare() -> None:
    gate_path = artifact_path(GATE_FILENAME)
    evidence_path = artifact_path(EVIDENCE_FILENAME)
    gate_path.unlink(missing_ok=True)
    evidence_path.unlink(missing_ok=True)
    document_id, version_id = register_and_upload()

    settings = get_settings()
    engine = build_engine(settings.database_url)
    try:
        deadline = monotonic() + TIMEOUT_SECONDS
        while monotonic() < deadline:
            with Session(engine) as session:
                version = session.get(DocumentVersion, version_id)
                if (
                    version is not None
                    and version.status is DocumentProcessingStatus.EXTRACTING
                    and version.processing_task_id is not None
                ):
                    evidence: dict[str, Any] = {
                        "document_id": str(document_id),
                        "version_id": str(version_id),
                        "processing_task_id": version.processing_task_id,
                        "attempt_count": version.attempt_count,
                    }
                    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
                    print(
                        "Claimed worker-loss setup passed: "
                        f"document_id={document_id} version_id={version_id} "
                        "status=extracting attempt=1 owner=present"
                    )
                    return
                if version is not None and version.status in {
                    DocumentProcessingStatus.READY,
                    DocumentProcessingStatus.FAILED,
                }:
                    raise RuntimeError(
                        "Worker-loss task reached terminal state before the kill"
                    )
            sleep(0.1)
    finally:
        engine.dispose()
    raise RuntimeError("Worker-loss task did not reach an owned extracting state")


def release() -> None:
    gate_path = artifact_path(GATE_FILENAME)
    if not artifact_path(EVIDENCE_FILENAME).is_file():
        raise RuntimeError("Worker-loss evidence is missing before release")
    gate_path.touch(exist_ok=True)
    print("Claimed worker-loss scanner gate released")


def verify() -> None:
    gate_path = artifact_path(GATE_FILENAME)
    evidence_path = artifact_path(EVIDENCE_FILENAME)
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        document_id = UUID(evidence["document_id"])
        version_id = UUID(evidence["version_id"])
        if evidence["attempt_count"] != 1 or not evidence["processing_task_id"]:
            raise RuntimeError("Initial claim evidence is incoherent")

        settings = get_settings()
        engine = build_engine(settings.database_url)
        try:
            deadline = monotonic() + TIMEOUT_SECONDS
            while monotonic() < deadline:
                with Session(engine) as session:
                    version = session.get(DocumentVersion, version_id)
                    if (
                        version is not None
                        and version.status is DocumentProcessingStatus.READY
                    ):
                        version_count = session.scalar(
                            select(func.count())
                            .select_from(DocumentVersion)
                            .where(DocumentVersion.document_id == document_id)
                        )
                        terminal_events = list(
                            session.scalars(
                                select(AuditEvent).where(
                                    AuditEvent.resource_id == version_id,
                                    AuditEvent.action.in_(
                                        [
                                            AuditAction.DOCUMENT_READY.value,
                                            AuditAction.DOCUMENT_FAILED.value,
                                        ]
                                    ),
                                )
                            )
                        )
                        if (
                            version_count != 1
                            or version.attempt_count != 1
                            or version.processing_task_id is not None
                            or version.processing_started_at is not None
                            or version.error_code is not None
                            or version.extracted_text != EXPECTED_TEXT
                            or len(terminal_events) != 1
                            or terminal_events[0].action
                            != AuditAction.DOCUMENT_READY.value
                        ):
                            raise RuntimeError(
                                "Worker-loss redelivery created incoherent effects"
                            )
                        print(
                            "Claimed worker-loss redelivery passed: "
                            f"document_id={document_id} version_id={version_id} "
                            "status=ready attempt=1 versions=1 terminal_audits=1"
                        )
                        return
                    if (
                        version is not None
                        and version.status is DocumentProcessingStatus.FAILED
                    ):
                        raise RuntimeError(
                            "Worker-loss redelivery reached a failed state"
                        )
                sleep(0.1)
        finally:
            engine.dispose()
        raise RuntimeError("Worker-loss redelivery did not reach ready")
    finally:
        gate_path.unlink(missing_ok=True)
        evidence_path.unlink(missing_ok=True)


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"prepare", "release", "verify"}:
        raise SystemExit("Usage: smoke_worker_redelivery prepare|release|verify")
    {"prepare": prepare, "release": release, "verify": verify}[sys.argv[1]]()


if __name__ == "__main__":
    main()
