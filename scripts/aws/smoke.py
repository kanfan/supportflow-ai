"""Run the synthetic application smoke used after an ECS release.

The account/environment supplies the synthetic admin through protected GitHub
environment secrets.  This script never prints request bodies, tokens, response
payloads, filenames, or document contents.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import UUID, uuid4


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"missing protected smoke input: {name}")
    return value


def _request(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any], dict[str, str]]:
    request = Request(
        f"{base_url}{path}",
        data=body,
        headers=headers or {},
        method=method,
    )
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310
            status = response.status
            response_headers = {
                key.lower(): value for key, value in response.headers.items()
            }
            raw = response.read()
    except HTTPError as exc:
        raise RuntimeError(f"{method} {path} returned HTTP {exc.code}") from None
    except URLError as exc:
        raise RuntimeError(f"{method} {path} was unreachable") from exc
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{method} {path} returned non-JSON data") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{method} {path} returned an invalid JSON object")
    return status, payload, response_headers


def _json_body(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _auth_headers(token: str, organization_id: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Organization-ID": organization_id,
    }


def _multipart_file(filename: str, content: bytes) -> tuple[bytes, str]:
    boundary = f"----supportflow-smoke-{uuid4().hex}"
    boundary_bytes = boundary.encode("ascii")
    body = b"--" + boundary_bytes
    body += (
        b'\r\nContent-Disposition: form-data; name="file"; filename="'
        + filename.encode("ascii")
    )
    body += b'"\r\nContent-Type: text/plain\r\n\r\n' + content
    body += b"\r\n--" + boundary_bytes + b"--\r\n"
    return body, f"multipart/form-data; boundary={boundary}"


def run(base_url: str) -> None:
    organization_id = _required("SUPPORTFLOW_SMOKE_ORGANIZATION_ID")
    email = _required("SUPPORTFLOW_SMOKE_EMAIL")
    password = _required("SUPPORTFLOW_SMOKE_PASSWORD")
    initial_body = _required("SUPPORTFLOW_SMOKE_INITIAL_BODY")
    followup_body = _required("SUPPORTFLOW_SMOKE_FOLLOWUP_BODY")
    try:
        UUID(organization_id)
    except ValueError as exc:
        raise RuntimeError("smoke organization id is not a UUID") from exc

    for path in ("/health/live", "/health/ready"):
        status, payload, _headers = _request(base_url, path)
        if status != 200 or payload.get("status") != "ok":
            raise RuntimeError(f"health smoke failed for {path}")

    status, login, _headers = _request(
        base_url,
        "/api/v1/auth/login",
        method="POST",
        body=_json_body({"email": email, "password": password}),
        headers={"Content-Type": "application/json"},
    )
    if status != 200 or not isinstance(login.get("access_token"), str):
        raise RuntimeError("synthetic login smoke failed")
    token = str(login["access_token"])
    headers = _auth_headers(token, organization_id)

    status, _me, _headers = _request(base_url, "/api/v1/auth/me", headers=headers)
    if status != 200:
        raise RuntimeError("authenticated identity smoke failed")

    ticket_payload = {
        "subject": f"AWS synthetic release smoke {uuid4().hex[:12]}",
        "source_type": "manual",
        "initial_message": {"body": initial_body, "author_type": "agent"},
    }
    status, ticket, _headers = _request(
        base_url,
        "/api/v1/tickets",
        method="POST",
        body=_json_body(ticket_payload),
        headers={**headers, "Content-Type": "application/json"},
    )
    if status != 201 or not isinstance(ticket.get("id"), str):
        raise RuntimeError("ticket creation smoke failed")
    ticket_id = str(ticket["id"])
    status, _message, _headers = _request(
        base_url,
        f"/api/v1/tickets/{ticket_id}/messages",
        method="POST",
        body=_json_body({"body": followup_body}),
        headers={**headers, "Content-Type": "application/json"},
    )
    if status != 201:
        raise RuntimeError("ticket follow-up smoke failed")
    status, _tickets, _headers = _request(
        base_url,
        "/api/v1/tickets?limit=10&offset=0",
        headers=headers,
    )
    if status != 200:
        raise RuntimeError("ticket list smoke failed")

    document_body, content_type = _multipart_file(
        "synthetic-release-smoke.txt",
        b"SupportFlow synthetic release evidence.\n",
    )
    status, document, response_headers = _request(
        base_url,
        "/api/v1/documents",
        method="POST",
        body=document_body,
        headers={**headers, "Content-Type": content_type},
    )
    if status != 202 or not isinstance(document.get("id"), str):
        raise RuntimeError("document upload smoke failed")
    document_id = str(document["id"])
    status_path = response_headers.get("location", f"/api/v1/documents/{document_id}")
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        status, document_status, _headers = _request(
            base_url,
            status_path,
            headers=headers,
        )
        if status != 200:
            raise RuntimeError("document status smoke failed")
        version = document_status.get("version")
        state = version.get("status") if isinstance(version, dict) else None
        if state == "ready":
            print("Synthetic authentication, ticket, document, and worker smoke passed")
            return
        if state == "failed":
            raise RuntimeError("document ingestion smoke reached failed state")
        time.sleep(5)
    raise RuntimeError("document ingestion smoke did not reach ready state")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise SystemExit("AWS smoke base URL must be an HTTPS origin")
    run(base_url)


if __name__ == "__main__":
    main()
