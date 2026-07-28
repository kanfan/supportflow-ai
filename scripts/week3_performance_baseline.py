"""Seed a fixed disposable dataset and measure the Week 3 ticket endpoints."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
import json
import math
import os
from pathlib import Path
import platform
import statistics
import subprocess
from time import perf_counter_ns
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, event, insert, text
from sqlalchemy.engine import make_url

from app.auth.security import PasswordManager
from app.config import Settings
from app.identity.models import Organization, OrganizationMember, User
from app.infrastructure.database import build_engine
from app.main import create_app
from app.tickets.models import Ticket, TicketMessage


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PERF_DATABASE_NAME = "supportflow_perf"
PASSWORD = "week3 performance password"
AUTH_SECRET = "week3-performance-secret-at-least-thirty-two-bytes"
TICKETS_PER_ORGANIZATION = 500
MESSAGES_PER_TICKET = 5


def stable_uuid(name: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"supportflow-week3-performance:{name}")


def reset_schema(database_url: str) -> None:
    database_name = make_url(database_url).database
    if database_name != PERF_DATABASE_NAME:
        raise RuntimeError(
            "Performance reset requires a disposable database named "
            f"{PERF_DATABASE_NAME}; received {database_name!r}"
        )
    config = Config(PROJECT_ROOT / "alembic.ini")
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    command.check(config)


def seed_dataset(engine: Engine) -> dict[str, Any]:
    organization_ids = [stable_uuid("organization-a"), stable_uuid("organization-b")]
    user_ids = [stable_uuid("user-a"), stable_uuid("user-b")]
    password_hash = PasswordManager().hash(PASSWORD)
    ticket_ids: list[list[UUID]] = [[], []]
    statuses = ("open", "processing", "waiting_for_agent", "resolved", "closed")

    organization_rows = [
        {
            "id": organization_ids[index],
            "name": f"Performance Organization {letter}",
            "slug": f"performance-{letter.lower()}",
            "status": "active",
        }
        for index, letter in enumerate(("A", "B"))
    ]
    user_rows = [
        {
            "id": user_ids[index],
            "email": f"performance-{letter.lower()}@example.com",
            "password_hash": password_hash,
            "status": "active",
        }
        for index, letter in enumerate(("A", "B"))
    ]
    membership_rows = [
        {
            "organization_id": organization_ids[index],
            "user_id": user_ids[index],
            "role": "admin",
            "status": "active",
        }
        for index in range(2)
    ]
    ticket_rows: list[dict[str, Any]] = []
    message_rows: list[dict[str, Any]] = []
    for organization_index, organization_id in enumerate(organization_ids):
        for ticket_index in range(TICKETS_PER_ORGANIZATION):
            ticket_id = stable_uuid(f"ticket-{organization_index}-{ticket_index:04d}")
            ticket_ids[organization_index].append(ticket_id)
            ticket_rows.append(
                {
                    "id": ticket_id,
                    "organization_id": organization_id,
                    "customer_id": None,
                    "source_type": "manual" if ticket_index % 2 == 0 else "api",
                    "external_id": f"perf-{organization_index}-{ticket_index}",
                    "subject": (
                        f"Performance ticket {organization_index}-{ticket_index:04d}"
                    ),
                    "status": statuses[ticket_index % len(statuses)],
                }
            )
            for message_index in range(MESSAGES_PER_TICKET):
                message_rows.append(
                    {
                        "id": stable_uuid(
                            "message-"
                            f"{organization_index}-{ticket_index:04d}-"
                            f"{message_index}"
                        ),
                        "organization_id": organization_id,
                        "ticket_id": ticket_id,
                        "author_type": "agent",
                        "author_user_id": user_ids[organization_index],
                        "author_customer_id": None,
                        "body": (
                            "Synthetic performance message "
                            f"{organization_index}-{ticket_index:04d}-"
                            f"{message_index}"
                        ),
                    }
                )

    with engine.begin() as connection:
        connection.execute(insert(Organization), organization_rows)
        connection.execute(insert(User), user_rows)
        connection.execute(insert(OrganizationMember), membership_rows)
        connection.execute(insert(Ticket), ticket_rows)
        connection.execute(insert(TicketMessage), message_rows)
        connection.execute(text("ANALYZE tickets"))
        connection.execute(text("ANALYZE ticket_messages"))

    return {
        "organization_ids": organization_ids,
        "user_ids": user_ids,
        "ticket_ids": ticket_ids,
        "open_ticket_ids": [
            [
                ticket_ids[organization_index][index]
                for index in range(0, TICKETS_PER_ORGANIZATION, len(statuses))
            ]
            for organization_index in range(2)
        ],
    }


class QueryCounter:
    def __init__(self, engine: Engine) -> None:
        self.count = 0
        self.active = False
        event.listen(engine, "before_cursor_execute", self._before_cursor_execute)

    def _before_cursor_execute(
        self,
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        del connection, cursor, statement, parameters, context, executemany
        if self.active:
            self.count += 1

    @contextmanager
    def capture(self) -> Any:
        self.count = 0
        self.active = True
        try:
            yield
        finally:
            self.active = False

    def close(self, engine: Engine) -> None:
        event.remove(engine, "before_cursor_execute", self._before_cursor_execute)


def percentile(values: list[float], percentile_value: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(percentile_value * len(ordered)) - 1)
    return ordered[index]


def measure(
    counter: QueryCounter,
    operation: Callable[[], Any],
    *,
    iterations: int,
) -> dict[str, Any]:
    durations: list[float] = []
    query_counts: list[int] = []
    for _ in range(iterations):
        started = perf_counter_ns()
        with counter.capture():
            response = operation()
        duration_ms = (perf_counter_ns() - started) / 1_000_000
        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError(
                f"Measured request returned unexpected {response.status_code}"
            )
        durations.append(duration_ms)
        query_counts.append(counter.count)
    return {
        "iterations": iterations,
        "median_ms": round(statistics.median(durations), 3),
        "p95_ms": round(percentile(durations, 0.95), 3),
        "min_ms": round(min(durations), 3),
        "max_ms": round(max(durations), 3),
        "sql_statements": {
            "median": statistics.median(query_counts),
            "min": min(query_counts),
            "max": max(query_counts),
        },
    }


def summarize_plan(plan_result: Any) -> dict[str, Any]:
    root = plan_result[0]["Plan"]
    nodes: list[str] = []
    indexes: list[str] = []

    def visit(node: dict[str, Any]) -> None:
        nodes.append(str(node["Node Type"]))
        if node.get("Index Name"):
            indexes.append(str(node["Index Name"]))
        for child in node.get("Plans", []):
            visit(child)

    visit(root)
    return {
        "execution_ms": round(float(plan_result[0]["Execution Time"]), 3),
        "planning_ms": round(float(plan_result[0]["Planning Time"]), 3),
        "root_node": root["Node Type"],
        "nodes": nodes,
        "indexes": sorted(set(indexes)),
        "shared_hit_blocks": root.get("Shared Hit Blocks", 0),
        "shared_read_blocks": root.get("Shared Read Blocks", 0),
        "rows": root.get("Actual Rows", 0),
    }


def explain_queries(
    engine: Engine,
    *,
    organization_id: UUID,
    ticket_id: UUID,
) -> dict[str, Any]:
    statements = {
        "ticket_list": text(
            """
            EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
            SELECT *
            FROM tickets
            WHERE organization_id = :organization_id AND status = 'open'
            ORDER BY created_at DESC, id DESC
            LIMIT 20 OFFSET 0
            """
        ),
        "ticket_detail": text(
            """
            EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
            SELECT *
            FROM tickets
            WHERE organization_id = :organization_id AND id = :ticket_id
            """
        ),
        "ticket_messages": text(
            """
            EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
            SELECT *
            FROM ticket_messages
            WHERE organization_id = :organization_id AND ticket_id = :ticket_id
            ORDER BY created_at ASC, id ASC
            """
        ),
    }
    summaries: dict[str, Any] = {}
    with engine.connect() as connection:
        for name, statement in statements.items():
            result = connection.execute(
                statement,
                {"organization_id": organization_id, "ticket_id": ticket_id},
            ).scalar_one()
            summaries[name] = summarize_plan(result)
    return summaries


def git_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def run(database_url: str, iterations: int) -> dict[str, Any]:
    if iterations < 10 or iterations > 90:
        raise RuntimeError("Iterations must be between 10 and 90")
    reset_schema(database_url)
    seed_engine = build_engine(database_url)
    dataset = seed_dataset(seed_engine)

    settings = Settings(
        environment="test",
        database_url=database_url,
        auth_secret_key=SecretStr(AUTH_SECRET),
        auth_issuer="supportflow-week3-performance",
        auth_audience="supportflow-week3-performance-api",
    )
    application = create_app(settings)
    app_engine: Engine = application.state.session_factory.kw["bind"]
    counter = QueryCounter(app_engine)
    organization_id = dataset["organization_ids"][0]
    detail_ticket_id = dataset["ticket_ids"][0][1]
    message_ticket_id = dataset["ticket_ids"][0][2]
    open_ticket_ids = dataset["open_ticket_ids"][0]

    with TestClient(application) as client:
        login_response = client.post(
            "/api/v1/auth/login",
            json={
                "email": "performance-a@example.com",
                "password": PASSWORD,
            },
        )
        if login_response.status_code != 200:
            raise RuntimeError("Performance identity could not log in")
        token = login_response.json()["access_token"]
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Organization-ID": str(organization_id),
        }

        for _ in range(5):
            client.get(
                "/api/v1/tickets",
                headers=headers,
                params={"status": "open", "limit": 20},
            )
            client.get(
                f"/api/v1/tickets/{detail_ticket_id}",
                headers=headers,
            )

        metrics = {
            "ticket_list": measure(
                counter,
                lambda: client.get(
                    "/api/v1/tickets",
                    headers=headers,
                    params={"status": "open", "limit": 20},
                ),
                iterations=iterations,
            ),
            "ticket_detail": measure(
                counter,
                lambda: client.get(
                    f"/api/v1/tickets/{detail_ticket_id}",
                    headers=headers,
                ),
                iterations=iterations,
            ),
        }

        message_index = 0

        def append_message() -> Any:
            nonlocal message_index
            message_index += 1
            return client.post(
                f"/api/v1/tickets/{message_ticket_id}/messages",
                headers=headers,
                json={"body": f"Measured agent message {message_index}"},
            )

        metrics["message_create"] = measure(
            counter,
            append_message,
            iterations=iterations,
        )

        status_index = 0

        def transition_status() -> Any:
            nonlocal status_index
            ticket_id = open_ticket_ids[status_index]
            status_index += 1
            return client.patch(
                f"/api/v1/tickets/{ticket_id}/status",
                headers=headers,
                json={"status": "processing"},
            )

        metrics["status_transition"] = measure(
            counter,
            transition_status,
            iterations=iterations,
        )

    counter.close(app_engine)
    plans = explain_queries(
        seed_engine,
        organization_id=organization_id,
        ticket_id=detail_ticket_id,
    )
    with seed_engine.connect() as connection:
        postgres_version = connection.execute(text("SHOW server_version")).scalar_one()
    seed_engine.dispose()
    app_engine.dispose()

    return {
        "measured_at": datetime.now(UTC).isoformat(),
        "commit": git_commit(),
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "postgresql": postgres_version,
            "measurement_client": "FastAPI TestClient in-process",
            "database": "local Docker PostgreSQL over TCP",
        },
        "dataset": {
            "organizations": 2,
            "users": 2,
            "tickets": 2 * TICKETS_PER_ORGANIZATION,
            "messages_before_measurement": (
                2 * TICKETS_PER_ORGANIZATION * MESSAGES_PER_TICKET
            ),
            "ticket_status_distribution": "even across five Week 2 statuses",
            "ticket_source_distribution": "50% manual, 50% api",
        },
        "method": {
            "warmup_iterations_for_reads": 5,
            "measured_iterations_per_operation": iterations,
            "latency_clock": "perf_counter_ns",
            "scope": "HTTP routing, authentication, tenant authorization, ORM, local DB",
        },
        "metrics": metrics,
        "query_plans": plans,
        "interpretation_limit": (
            "Local engineering baseline, not a production SLA or network/load test."
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--database-url",
        default=os.getenv("SUPPORTFLOW_PERF_DATABASE_URL"),
    )
    parser.add_argument("--iterations", type=int, default=40)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.database_url:
        raise RuntimeError(
            "Set SUPPORTFLOW_PERF_DATABASE_URL or pass --database-url; "
            f"the database name must be {PERF_DATABASE_NAME}"
        )
    result = run(args.database_url, args.iterations)
    serialized = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
