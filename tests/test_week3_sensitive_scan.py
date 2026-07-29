from scripts.week3_sensitive_scan import scan_sensitive_text


def test_sensitive_scan_accepts_allowlisted_audit_metadata() -> None:
    text = """
    {"action":"ticket.created","metadata":{"source_type":"manual"}}
    {"action":"ticket.status_changed","metadata":{
      "previous_status":"open","new_status":"processing"
    }}
    """

    assert scan_sensitive_text(text) == []


def test_sensitive_scan_reports_rules_and_lines_without_values() -> None:
    canary = "customer-message-canary"
    text = "\n".join(
        (
            "clean line",
            "person@example.com",
            '{"access_token":"hidden-value"}',
            canary,
        )
    )

    findings = scan_sensitive_text(text, forbidden_values=(canary,))

    assert [(finding.rule, finding.line) for finding in findings] == [
        ("email_address", 2),
        ("sensitive_key_value", 3),
        ("forbidden_canary_1", 4),
    ]
    assert all(canary not in repr(finding) for finding in findings)


def test_sensitive_scan_detects_bearer_headers_and_jwts() -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnopqrstuvwxyz"

    findings = scan_sensitive_text(f"Authorization: Bearer {jwt}")

    assert {finding.rule for finding in findings} == {
        "authorization_header",
        "jwt",
    }


def test_release_log_scan_canaries_match_password_and_body_prefixes() -> None:
    forbidden_values = (
        "week3 demo password 2026",
        "initial-body-",
        "follow-up-body-",
    )
    text = "\n".join(
        (
            "week3 demo password 2026",
            "initial-body-randomsuffix",
            "follow-up-body-randomsuffix",
        )
    )

    findings = scan_sensitive_text(text, forbidden_values=forbidden_values)

    assert [finding.rule for finding in findings] == [
        "forbidden_canary_1",
        "forbidden_canary_2",
        "forbidden_canary_3",
    ]
