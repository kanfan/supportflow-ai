"""Detect likely secrets and direct identifiers without echoing their values."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import sys


@dataclass(frozen=True)
class SensitiveFinding:
    rule: str
    line: int


SENSITIVE_PATTERNS = {
    "authorization_header": re.compile(
        r"\bauthorization\b\s*[:=]\s*[\"']?bearer\s+\S+",
        re.IGNORECASE,
    ),
    "email_address": re.compile(
        r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        re.IGNORECASE,
    ),
    "jwt": re.compile(
        r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"
    ),
    "sensitive_key_value": re.compile(
        r"""[\"']?(?:password|secret|access_token|refresh_token)[\"']?"""
        r"""\s*[:=]\s*[\"'][^\"'\r\n]+[\"']""",
        re.IGNORECASE,
    ),
}


def scan_sensitive_text(
    text: str,
    *,
    forbidden_values: tuple[str, ...] = (),
) -> list[SensitiveFinding]:
    """Return rule names and locations while never returning matched values."""

    findings: list[SensitiveFinding] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for rule, pattern in SENSITIVE_PATTERNS.items():
            if pattern.search(line):
                findings.append(SensitiveFinding(rule=rule, line=line_number))
        for index, value in enumerate(forbidden_values, start=1):
            if value and value in line:
                findings.append(
                    SensitiveFinding(
                        rule=f"forbidden_canary_{index}",
                        line=line_number,
                    )
                )
    return findings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fail when a text file or stdin contains likely credentials, "
            "email addresses, JWTs, or explicit canary values."
        )
    )
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        help="Text file to scan. Reads stdin when omitted.",
    )
    parser.add_argument(
        "--forbid",
        action="append",
        default=[],
        help="Exact canary value that must not occur. May be repeated.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    text = (
        args.path.read_text(encoding="utf-8", errors="replace")
        if args.path
        else sys.stdin.read()
    )
    findings = scan_sensitive_text(text, forbidden_values=tuple(args.forbid))
    if findings:
        for finding in findings:
            print(f"{finding.rule} at line {finding.line}")
        print(f"Sensitive-output scan failed with {len(findings)} finding(s).")
        return 1
    print("Sensitive-output scan passed: no configured patterns were found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
