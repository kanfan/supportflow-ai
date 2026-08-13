from datetime import UTC, datetime, timedelta
from io import BytesIO
from struct import pack

import pytest

from app.documents.clamd import (
    CLAMD_STREAM_CHUNK_BYTES,
    ClamdDocumentSafetyScanner,
    parse_clamd_database_timestamp,
)
from app.documents.ports import DocumentScanResult


NOW = datetime(2026, 8, 13, 12, 0, 0, tzinfo=UTC)
FRESH_VERSION = "ClamAV 1.4.3/27817/Thu Aug 13 12:00:00 2026"


class ScriptedSocket:
    def __init__(self, reply: bytes | BaseException) -> None:
        self.reply = reply
        self.sent: list[bytes] = []
        self.timeouts: list[float | None] = []
        self._read = False

    def __enter__(self) -> "ScriptedSocket":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def settimeout(self, value: float | None) -> None:
        self.timeouts.append(value)

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def recv(self, _bufsize: int) -> bytes:
        if isinstance(self.reply, BaseException):
            raise self.reply
        if self._read:
            return b""
        self._read = True
        return self.reply


class ScriptedConnectionFactory:
    def __init__(self, *replies: bytes | BaseException) -> None:
        self.sockets = [ScriptedSocket(reply) for reply in replies]
        self.calls: list[tuple[tuple[str, int], float]] = []

    def __call__(self, address: tuple[str, int], timeout: float) -> ScriptedSocket:
        self.calls.append((address, timeout))
        return self.sockets[len(self.calls) - 1]


def build_scanner(
    *replies: bytes | BaseException,
    now: datetime = NOW,
    max_stream_bytes: int = 10 * 1024 * 1024,
) -> tuple[ClamdDocumentSafetyScanner, ScriptedConnectionFactory]:
    factory = ScriptedConnectionFactory(*replies)
    scanner = ClamdDocumentSafetyScanner(
        host="127.0.0.1",
        port=3310,
        connect_timeout_seconds=2,
        scan_timeout_seconds=60,
        max_stream_bytes=max_stream_bytes,
        now=lambda: now,
        connection_factory=factory,
    )
    return scanner, factory


def test_clean_scan_uses_ping_version_and_bounded_instream_framing() -> None:
    scanner, factory = build_scanner(
        b"PONG\0",
        FRESH_VERSION.encode() + b"\0",
        b"stream: OK\0",
    )
    content = b"safe document"

    result = scanner.scan(BytesIO(content))

    assert result is DocumentScanResult.CLEAN
    assert [call[0] for call in factory.calls] == [
        ("127.0.0.1", 3310),
        ("127.0.0.1", 3310),
        ("127.0.0.1", 3310),
    ]
    assert factory.sockets[0].sent == [b"zPING\0"]
    assert factory.sockets[1].sent == [b"zVERSION\0"]
    assert factory.sockets[2].sent == [
        b"zINSTREAM\0",
        pack(">I", len(content)) + content,
        pack(">I", 0),
    ]
    assert all(
        timeout is not None and timeout <= 2 for timeout in factory.sockets[0].timeouts
    )
    assert all(
        timeout is not None and timeout <= 60 for timeout in factory.sockets[2].timeouts
    )


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (b"stream: Eicar-Signature FOUND\0", DocumentScanResult.INFECTED),
        (b"stream: scanner failure ERROR\0", DocumentScanResult.UNAVAILABLE),
        (b"unexpected\0", DocumentScanResult.UNAVAILABLE),
        (b"stream:  FOUND\0", DocumentScanResult.UNAVAILABLE),
        (b"stream: \xff\0", DocumentScanResult.UNAVAILABLE),
        (b"x" * 4097 + b"\0", DocumentScanResult.UNAVAILABLE),
    ],
)
def test_scan_maps_only_allowlisted_clamd_outcomes(
    reply: bytes,
    expected: DocumentScanResult,
) -> None:
    scanner, _factory = build_scanner(
        b"PONG\0",
        FRESH_VERSION.encode() + b"\0",
        reply,
    )

    assert scanner.scan(BytesIO(b"fixture")) is expected


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        (NOW - timedelta(hours=24), True),
        (NOW - timedelta(hours=24, seconds=1), False),
        (NOW + timedelta(minutes=5), True),
        (NOW + timedelta(minutes=5, seconds=1), False),
    ],
)
def test_signature_freshness_boundaries(
    timestamp: datetime,
    expected: bool,
) -> None:
    version = timestamp.strftime("ClamAV 1.4.3/27817/%a %b %d %H:%M:%S %Y")
    scanner, _factory = build_scanner(b"PONG\0", version.encode() + b"\0")

    assert scanner.is_healthy() is expected


@pytest.mark.parametrize(
    "version",
    [
        "COMMAND UNAVAILABLE",
        "ClamAV 1.4.3/27817/no timestamp",
        "ClamAV 1.4.3/27817/Thu Feb 30 12:00:00 2026",
    ],
)
def test_health_fails_closed_for_missing_or_malformed_database_timestamp(
    version: str,
) -> None:
    scanner, _factory = build_scanner(b"PONG\0", version.encode() + b"\0")

    assert scanner.is_healthy() is False


def test_health_requires_exact_pong() -> None:
    scanner, factory = build_scanner(b"NOT READY\0")

    assert scanner.is_healthy() is False
    assert len(factory.calls) == 1


def test_transport_timeout_fails_closed() -> None:
    scanner, _factory = build_scanner(TimeoutError("provider detail canary"))

    assert scanner.scan(BytesIO(b"fixture")) is DocumentScanResult.UNAVAILABLE


def test_stream_larger_than_application_limit_fails_closed() -> None:
    scanner, factory = build_scanner(
        b"PONG\0",
        FRESH_VERSION.encode() + b"\0",
        b"stream: OK\0",
        max_stream_bytes=3,
    )

    assert scanner.scan(BytesIO(b"four")) is DocumentScanResult.UNAVAILABLE
    assert factory.sockets[2].sent == [b"zINSTREAM\0"]


def test_stream_is_split_into_fixed_64_kib_chunks() -> None:
    scanner, factory = build_scanner(
        b"PONG\0",
        FRESH_VERSION.encode() + b"\0",
        b"stream: OK\0",
    )
    content = b"x" * (CLAMD_STREAM_CHUNK_BYTES + 1)

    assert scanner.scan(BytesIO(content)) is DocumentScanResult.CLEAN
    assert len(factory.sockets[2].sent[1]) == 4 + CLAMD_STREAM_CHUNK_BYTES
    assert factory.sockets[2].sent[2] == pack(">I", 1) + b"x"
    assert factory.sockets[2].sent[3] == pack(">I", 0)


def test_provider_reply_and_signature_are_not_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    canary = "do-not-log-signature-canary"
    scanner, _factory = build_scanner(
        b"PONG\0",
        FRESH_VERSION.encode() + b"\0",
        f"stream: {canary} FOUND\0".encode(),
    )

    assert scanner.scan(BytesIO(b"fixture")) is DocumentScanResult.INFECTED
    assert canary not in caplog.text


def test_version_parser_is_locale_independent_and_utc() -> None:
    assert parse_clamd_database_timestamp(FRESH_VERSION) == NOW
    assert parse_clamd_database_timestamp(
        "ClamAV 1.4.3/27817/Fri Aug  7 12:00:00 2026"
    ) == datetime(2026, 8, 7, 12, 0, 0, tzinfo=UTC)
    assert parse_clamd_database_timestamp("invalid") is None
