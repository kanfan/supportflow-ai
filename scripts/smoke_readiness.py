import argparse
import json
from typing import Literal
from urllib.error import HTTPError
from urllib.request import urlopen
from uuid import UUID

from app.observability import REQUEST_ID_HEADER


ReadinessExpectation = Literal["ready", "dependency-failure"]


def request(path: str) -> tuple[int, dict[str, object], str]:
    try:
        with urlopen(f"http://127.0.0.1:8000{path}", timeout=10) as response:  # noqa: S310
            status_code = response.status
            payload = json.loads(response.read())
            request_id = response.headers[REQUEST_ID_HEADER]
    except HTTPError as exc:
        status_code = exc.code
        payload = json.loads(exc.read())
        request_id = exc.headers[REQUEST_ID_HEADER]
    if not isinstance(payload, dict):
        raise RuntimeError("Health response must be a JSON object")
    if UUID(request_id).version != 4:
        raise RuntimeError("Health response is missing a valid request ID")
    return status_code, payload, request_id


def main(expectation: ReadinessExpectation) -> None:
    live_status, live_payload, live_request_id = request("/health/live")
    if live_status != 200 or live_payload != {"status": "ok"}:
        raise RuntimeError("Liveness changed with dependency state")

    ready_status, ready_payload, ready_request_id = request("/health/ready")
    expected = (
        (200, {"status": "ok"})
        if expectation == "ready"
        else (503, {"status": "unavailable"})
    )
    if (ready_status, ready_payload) != expected:
        raise RuntimeError("Readiness did not match the expected dependency state")
    if ready_request_id == live_request_id:
        raise RuntimeError("Request IDs must be unique per request")

    print(f"Readiness smoke passed: expectation={expectation}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "expectation",
        choices=("ready", "dependency-failure"),
    )
    arguments = parser.parse_args()
    main(arguments.expectation)
