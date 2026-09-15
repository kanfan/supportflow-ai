"""Destructive fault injection ONLY in the disposable Compose CI stack.

The workflow stops worker/relay before either mode. No FLUSHDB/FLUSHALL: remove
only messages whose immutable task ID matches this script's uploaded version.
"""

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.engine import make_url

from app.config import get_settings
from app.documents.ingestion import INGEST_DOCUMENT_VERSION_TASK
from app.documents.models import (
    DocumentIngestionIntent as Intent,
    DocumentVersion as Version,
    DocumentProcessingStatus as Status,
)
from app.documents.relay import IngestionRelay, Publication, RelayPolicy
from app.infrastructure.database import build_engine, build_session_factory
from app.infrastructure.redis import build_redis_client
from app.worker import create_celery_client
from scripts.smoke_worker_redelivery import (
    artifact_path,
    EVIDENCE_FILENAME,
    GATE_FILENAME,
    register_and_upload,
)


def main() -> None:
    settings = get_settings()
    url = make_url(settings.database_url.get_secret_value())
    if (
        os.getenv("SUPPORTFLOW_OUTBOX_FAULT_SMOKE") != "1"
        or settings.environment != "local"
        or url.host != "postgres"
        or url.database != "supportflow"
        or len(sys.argv) != 2
        or sys.argv[1] not in {"queued-loss", "claimed-loss"}
    ):
        raise SystemExit("Requires explicitly opted-in disposable Compose fault smoke")
    mode = sys.argv[1]
    engine = build_engine(
        settings.database_url.get_secret_value(), connect_timeout_seconds=2
    )
    redis = build_redis_client(
        settings.celery_broker_url.get_secret_value(), timeout_seconds=2
    )
    client = create_celery_client(settings)

    def publish(job: Publication) -> None:
        client.send_task(
            INGEST_DOCUMENT_VERSION_TASK,
            args=[str(job.version_id)],
            task_id=job.task_id,
            retries=job.retries,
            retry=False,
            ignore_result=True,
        )

    # Zero stale deadline is test-only, AFTER workflow confirmed SIGKILL. Normal
    # runtime uses hard-limit + visibility + 60s and does not expose this override.
    relay = IngestionRelay(
        build_session_factory(engine), publish, RelayPolicy(extracting_seconds=0)
    )
    try:
        if mode == "queued-loss":
            artifact_path(GATE_FILENAME).touch()
            document_id, version_id = register_and_upload()
        else:
            evidence = json.loads(artifact_path(EVIDENCE_FILENAME).read_text())
            document_id, version_id = (
                UUID(evidence["document_id"]),
                UUID(evidence["version_id"]),
            )
        with Session(engine) as session, session.begin():
            intent = session.scalar(
                select(Intent).where(Intent.document_version_id == version_id)
            )
            version = session.get(Version, version_id)
            assert intent and version
            assert intent.lease_token is None
            task_id, intent_id = intent.task_id, intent.id
            if mode == "claimed-loss":
                assert (
                    version.status == Status.EXTRACTING
                    and version.processing_task_id == task_id
                )
            else:
                assert version.status == Status.QUEUED
                artifact_path(EVIDENCE_FILENAME).write_text(
                    json.dumps(
                        {
                            "document_id": str(document_id),
                            "version_id": str(version_id),
                            "processing_task_id": task_id,
                            "attempt_count": 1,
                        }
                    )
                )
            intent.available_at = datetime.now(UTC) - timedelta(days=1)
        if mode == "queued-loss":
            job = relay.claim()
            assert job and job.version_id == version_id
            publish(job)
            assert relay.finish(job, success=True)
        removed = 0
        for message in redis.lrange("celery", 0, -1):
            if json.loads(message)["headers"].get("id") == task_id:
                removed += redis.lrem("celery", 0, message)
        for tag, payload in redis.hgetall("unacked").items():
            if json.loads(payload)[0]["headers"].get("id") == task_id:
                with redis.pipeline() as pipe:
                    pipe.hdel("unacked", tag)
                    pipe.zrem("unacked_index", tag)
                    removed += int(pipe.execute()[0])
        assert removed >= 1, "No target broker message removed: loss not proven"
        with Session(engine) as session, session.begin():
            intent = session.get(Intent, intent_id)
            assert intent
            intent.available_at = datetime.now(UTC) - timedelta(days=1)
        recovered = relay.claim()
        assert (
            recovered
            and recovered.version_id == version_id
            and recovered.task_id == task_id
        )
        publish(recovered)
        assert relay.finish(recovered, success=True)
        artifact_path(GATE_FILENAME).touch()
        print(
            f"outbox_fault mode={mode} removed={removed} stable_identity=true republished=true shortened_deadline={mode == 'claimed-loss'}"
        )
    finally:
        client.close()
        redis.close()
        engine.dispose()


if __name__ == "__main__":
    main()
