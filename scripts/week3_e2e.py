"""Run the Week 3 two-user/two-tenant release scenario against a live API."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

import httpx

from app.ui.session import UI_SESSION_COOKIE
from scripts.week3_sensitive_scan import scan_sensitive_text


DEFAULT_PASSWORD = "week3 demo password 2026"


def require(response: httpx.Response, status_code: int) -> httpx.Response:
    if response.status_code != status_code:
        raise RuntimeError(
            f"{response.request.method} {response.request.url.path} returned "
            f"{response.status_code}; expected {status_code}"
        )
    return response


def csrf_from_html(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    if match is None:
        raise RuntimeError("CSRF token was absent from the rendered form")
    return match.group(1)


def register(
    client: httpx.Client,
    *,
    email: str,
    organization_name: str,
    organization_slug: str,
    password: str,
) -> dict[str, Any]:
    response = require(
        client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": password,
                "organization_name": organization_name,
                "organization_slug": organization_slug,
            },
        ),
        201,
    )
    return response.json()


def login(client: httpx.Client, *, email: str, password: str) -> str:
    response = require(
        client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
        ),
        200,
    )
    return str(response.json()["access_token"])


def tenant_headers(token: str, organization_id: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Organization-ID": organization_id,
    }


def create_ticket(
    client: httpx.Client,
    *,
    headers: dict[str, str],
    subject: str,
    body: str,
) -> dict[str, Any]:
    return require(
        client.post(
            "/api/v1/tickets",
            headers=headers,
            json={
                "subject": subject,
                "initial_message": {"author_type": "agent", "body": body},
            },
        ),
        201,
    ).json()


def run(base_url: str, password: str) -> dict[str, Any]:
    suffix = uuid4().hex[:12]
    admin_email = f"week3-admin-{suffix}@example.com"
    agent_email = f"week3-agent-{suffix}@example.com"
    other_email = f"week3-other-{suffix}@example.com"
    admin_slug = f"week3-admin-{suffix}"
    other_slug = f"week3-other-{suffix}"
    initial_canary = f"initial-body-{suffix}"
    follow_up_canary = f"follow-up-body-{suffix}"
    other_subject = f"other-tenant-subject-{suffix}"

    with httpx.Client(
        base_url=base_url.rstrip("/"),
        timeout=20,
        follow_redirects=False,
    ) as client:
        require(client.get("/health/live"), 200)
        admin = register(
            client,
            email=admin_email,
            organization_name="Week 3 Admin Organization",
            organization_slug=admin_slug,
            password=password,
        )
        register(
            client,
            email=agent_email,
            organization_name="Week 3 Agent Home",
            organization_slug=f"week3-agent-{suffix}",
            password=password,
        )
        other = register(
            client,
            email=other_email,
            organization_name="Week 3 Other Tenant",
            organization_slug=other_slug,
            password=password,
        )

        admin_token = login(client, email=admin_email, password=password)
        agent_token = login(client, email=agent_email, password=password)
        other_token = login(client, email=other_email, password=password)
        organization_id = str(admin["organization"]["id"])
        other_organization_id = str(other["organization"]["id"])
        admin_headers = tenant_headers(admin_token, organization_id)
        agent_headers = tenant_headers(agent_token, organization_id)
        other_headers = tenant_headers(other_token, other_organization_id)

        require(
            client.post(
                "/api/v1/organization-members",
                headers=admin_headers,
                json={"email": agent_email, "role": "agent"},
            ),
            201,
        )
        require(
            client.get("/api/v1/audit-events", headers=agent_headers),
            403,
        )
        require(
            client.post(
                "/api/v1/organization-members",
                headers=agent_headers,
                json={"email": other_email, "role": "agent"},
            ),
            403,
        )

        visible_ticket = create_ticket(
            client,
            headers=agent_headers,
            subject=f"visible-tenant-subject-{suffix}",
            body=initial_canary,
        )
        hidden_ticket = create_ticket(
            client,
            headers=other_headers,
            subject=other_subject,
            body=f"other-tenant-body-{suffix}",
        )

        ticket_list = require(
            client.get("/api/v1/tickets", headers=agent_headers),
            200,
        ).json()
        serialized_list = json.dumps(ticket_list)
        if other_subject in serialized_list:
            raise RuntimeError("Tenant A list disclosed Tenant B content")

        missing_id_response = require(
            client.get(
                f"/api/v1/tickets/{uuid4()}",
                headers=agent_headers,
            ),
            404,
        )
        cross_tenant_response = require(
            client.get(
                f"/api/v1/tickets/{hidden_ticket['id']}",
                headers=agent_headers,
            ),
            404,
        )
        if missing_id_response.json() != cross_tenant_response.json():
            raise RuntimeError("Missing and cross-tenant ticket responses differ")

        require(
            client.post(
                f"/api/v1/tickets/{visible_ticket['id']}/messages",
                headers=agent_headers,
                json={"body": "forged", "author_user_id": str(uuid4())},
            ),
            422,
        )
        require(
            client.patch(
                f"/api/v1/tickets/{visible_ticket['id']}/status",
                headers=agent_headers,
                json={"status": "resolved"},
            ),
            409,
        )
        after_invalid = require(
            client.get(
                f"/api/v1/tickets/{visible_ticket['id']}",
                headers=agent_headers,
            ),
            200,
        ).json()
        if after_invalid["status"] != "open":
            raise RuntimeError("Invalid transition changed ticket state")

        login_page = require(client.get("/ui/login"), 200)
        anonymous_cookie = client.cookies.get(UI_SESSION_COOKIE)
        if anonymous_cookie is None:
            raise RuntimeError("Anonymous UI session cookie was not issued")
        anonymous_csrf = csrf_from_html(login_page.text)
        require(
            client.post(
                "/ui/login",
                data={
                    "csrf_token": anonymous_csrf,
                    "email": agent_email,
                    "password": password,
                    "organization_slug": admin_slug,
                },
            ),
            303,
        )
        authenticated_cookie = client.cookies.get(UI_SESSION_COOKIE)
        if authenticated_cookie is None or authenticated_cookie == anonymous_cookie:
            raise RuntimeError("Login did not rotate the UI session")

        client.cookies.set(UI_SESSION_COOKIE, anonymous_cookie)
        replay_before_logout = require(client.get("/ui/tickets"), 303)
        if replay_before_logout.headers.get("location") != "/ui/login":
            raise RuntimeError("Pre-authentication session replay was accepted")
        client.cookies.set(UI_SESSION_COOKIE, authenticated_cookie)

        detail_page = require(
            client.get(f"/ui/tickets/{visible_ticket['id']}"),
            200,
        )
        authenticated_csrf = csrf_from_html(detail_page.text)
        require(
            client.post(
                f"/ui/tickets/{visible_ticket['id']}/messages",
                data={"csrf_token": authenticated_csrf, "body": follow_up_canary},
            ),
            303,
        )
        require(
            client.post(
                f"/ui/tickets/{visible_ticket['id']}/status",
                data={"csrf_token": authenticated_csrf, "status": "processing"},
            ),
            303,
        )

        final_detail = require(
            client.get(
                f"/api/v1/tickets/{visible_ticket['id']}",
                headers=admin_headers,
            ),
            200,
        ).json()
        if final_detail["status"] != "processing":
            raise RuntimeError("Valid UI transition did not persist")

        audit_payload = require(
            client.get(
                "/api/v1/audit-events?limit=100&offset=0",
                headers=admin_headers,
            ),
            200,
        ).json()
        audit_actions = {item["action"] for item in audit_payload["items"]}
        expected_actions = {
            "organization_member.created",
            "ticket.created",
            "ticket_message.created",
            "ticket.status_changed",
        }
        if not expected_actions.issubset(audit_actions):
            raise RuntimeError("Expected workflow audit actions are missing")
        sensitive_findings = scan_sensitive_text(
            json.dumps(audit_payload),
            forbidden_values=(
                password,
                admin_email,
                agent_email,
                other_email,
                admin_token,
                agent_token,
                other_token,
                initial_canary,
                follow_up_canary,
            ),
        )
        if sensitive_findings:
            rules = sorted({finding.rule for finding in sensitive_findings})
            raise RuntimeError(f"Audit sensitive-output scan failed: {rules}")

        require(client.post("/api/v1/documents", json={}), 404)
        require(
            client.post(
                "/ui/logout",
                data={"csrf_token": authenticated_csrf},
            ),
            303,
        )
        client.cookies.set(UI_SESSION_COOKIE, authenticated_cookie)
        replay_after_logout = require(client.get("/ui/tickets"), 303)
        if replay_after_logout.headers.get("location") != "/ui/login":
            raise RuntimeError("Logged-out session replay was accepted")

    return {
        "completed_at": datetime.now(UTC).isoformat(),
        "scenario": "fresh-db two-user/two-tenant API and UI workflow",
        "checks": {
            "health": "passed",
            "admin_adds_agent": "passed",
            "agent_admin_surface_blocked": "passed",
            "tenant_list_isolation": "passed",
            "cross_tenant_id_matches_missing_404": "passed",
            "author_impersonation_rejected": "passed",
            "invalid_transition_atomic": "passed",
            "ui_session_and_csrf_rotation": "passed",
            "ui_message_and_status_workflow": "passed",
            "audit_actions_present": sorted(expected_actions),
            "audit_sensitive_output_scan": "passed",
            "unsupported_upload_route_closed": "passed",
            "logout_replay_blocked": "passed",
        },
        "counts": {
            "tenant_a_visible_tickets": ticket_list["pagination"]["total"],
            "tenant_a_audit_events": audit_payload["pagination"]["total"],
            "ticket_messages_after_workflow": len(final_detail["messages"]),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run(args.base_url, args.password)
    serialized = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
