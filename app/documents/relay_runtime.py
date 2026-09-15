"""Normal Compose relay process and local-only health endpoint."""

import argparse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging
import signal
from threading import Event, Thread
from time import monotonic
from typing import cast
from uuid import UUID

from sqlalchemy import func, select, text

from app.config import get_settings
from app.documents.ingestion import INGEST_DOCUMENT_VERSION_TASK
from app.documents.models import DocumentIngestionIntent as Intent
from app.documents.relay import IngestionRelay, Publication, RelayPolicy, backfill
from app.documents.recovery import rearm_intent
from app.infrastructure.database import build_engine, build_session_factory
from app.infrastructure.redis import build_redis_client
from app.worker import create_celery_client


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill-cutoff", type=datetime.fromisoformat)
    parser.add_argument("--recover-intent", type=UUID)
    parser.add_argument("--organization-id", type=UUID)
    parser.add_argument("--actor-user-id", type=UUID)
    parser.add_argument("--dependencies-repaired", action="store_true")
    args = parser.parse_args()
    if not args.recover_intent and (
        args.organization_id or args.actor_user_id or args.dependencies_repaired
    ):
        parser.error("Recovery options require --recover-intent")
    if args.recover_intent and (
        args.backfill_cutoff
        or not args.organization_id
        or not args.actor_user_id
        or not args.dependencies_repaired
    ):
        parser.error(
            "Single-intent recovery requires tenant, actor and repaired-dependency confirmation; no backfill"
        )
    settings = get_settings()
    engine = build_engine(
        settings.database_url.get_secret_value(),
        connect_timeout_seconds=2,
        ssl_root_cert_path=settings.database_ssl_root_cert_path,
    )
    sessions = build_session_factory(engine)
    if args.recover_intent:
        probe = build_redis_client(
            settings.celery_broker_url.get_secret_value(), timeout_seconds=2
        )
        try:
            grant = rearm_intent(
                sessions,
                organization_id=args.organization_id,
                intent_id=args.recover_intent,
                actor_user_id=args.actor_user_id,
                verify_dependencies=probe.ping,
                extracting_seconds=settings.document_ingestion_hard_time_limit_seconds
                + settings.celery_visibility_timeout_seconds
                + 60,
                max_worker_attempts=settings.document_ingestion_max_retries + 1,
            )
            print(
                json.dumps({"rearmed": str(args.recover_intent), "grant_number": grant})
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from None
        except Exception:
            raise SystemExit(
                "Recovery failed: dependency or database unavailable"
            ) from None
        finally:
            probe.close()
            engine.dispose()
        return
    if args.backfill_cutoff is not None:
        try:
            print(
                json.dumps(
                    {"backfilled": backfill(sessions, cutoff=args.backfill_cutoff)}
                )
            )
        finally:
            engine.dispose()
        return
    client = create_celery_client(settings)
    client.conf.broker_transport_options.update(
        socket_connect_timeout=2, socket_timeout=2, retry_on_timeout=False
    )
    client.conf.broker_connection_timeout = 2
    client.conf.task_publish_retry = False
    redis = build_redis_client(
        settings.celery_broker_url.get_secret_value(), timeout_seconds=2
    )
    stopped = Event()
    state: dict[str, object] = {"last_poll": 0.0, "ready": False}

    def publish(job: Publication) -> None:
        client.send_task(
            INGEST_DOCUMENT_VERSION_TASK,
            args=[str(job.version_id)],
            task_id=job.task_id,
            retries=job.retries,
            retry=False,
            ignore_result=True,
        )

    relay = IngestionRelay(
        sessions,
        publish,
        RelayPolicy(
            recovery_seconds=settings.outbox_effective_recovery_seconds,
            extracting_seconds=settings.document_ingestion_hard_time_limit_seconds
            + settings.celery_visibility_timeout_seconds
            + 60,
        ),
    )

    class Health(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            fresh = monotonic() - cast(float, state["last_poll"]) < 60
            ready = fresh and (self.path == "/live" or bool(state["ready"]))
            self.send_response(200 if ready else 503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(state).encode())

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 8091), Health)
    Thread(target=server.serve_forever, daemon=True).start()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopped.set())
    try:
        while not stopped.is_set():
            try:
                # Bounded statements and one publication per poll; no busy spin.
                with sessions() as session:
                    session.execute(text("SET LOCAL statement_timeout = '2000ms'"))
                    count, oldest, unresolved = session.execute(
                        select(
                            func.count(Intent.id).filter(Intent.settled_at.is_(None)),
                            func.min(Intent.created_at).filter(
                                Intent.settled_at.is_(None)
                            ),
                            func.count(Intent.id).filter(
                                Intent.unresolved_at.is_not(None)
                            ),
                        )
                    ).one()
                state.update(
                    backlog=count,
                    oldest_created_at=oldest.isoformat() if oldest else None,
                    unresolved=unresolved,
                )
                relay.tick()
                relay.cleanup()
                state["ready"] = bool(redis.ping())
            except Exception:
                state["ready"] = False
                logging.warning(
                    "outbox_poll_failed error_category=dependency_unavailable"
                )
            state["last_poll"] = monotonic()
            stopped.wait(settings.outbox_poll_seconds)
    finally:
        server.shutdown()
        server.server_close()
        redis.close()
        client.close()
        engine.dispose()


if __name__ == "__main__":
    main()
