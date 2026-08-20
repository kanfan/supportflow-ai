from __future__ import annotations

from datetime import UTC, datetime, timedelta
import re
import socket
from struct import pack
from time import monotonic
from typing import BinaryIO, Callable, Protocol, cast

from app.documents.ports import DocumentScanResult


CLAMD_DEFAULT_HOST = "127.0.0.1"
CLAMD_DEFAULT_PORT = 3310
CLAMD_STREAM_CHUNK_BYTES = 64 * 1024
CLAMD_MAX_REPLY_BYTES = 4 * 1024

_VERSION_TIMESTAMP = re.compile(
    r"^ClamAV [^/]+/[^/]+/"
    r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) +"
    r"(?P<month>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) +"
    r"(?P<day>\d{1,2}) +"
    r"(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2}) +"
    r"(?P<year>\d{4})$"
)
_MONTHS = {
    month: number
    for number, month in enumerate(
        (
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        ),
        start=1,
    )
}


class _Socket(Protocol):
    def __enter__(self) -> "_Socket": ...

    def __exit__(self, *args: object) -> None: ...

    def settimeout(self, value: float | None, /) -> None: ...

    def sendall(self, data: bytes, /) -> None: ...

    def recv(self, bufsize: int, /) -> bytes: ...


def parse_clamd_database_timestamp(version_reply: str) -> datetime | None:
    """Parse the UTC database timestamp exposed by ClamD VERSION."""

    match = _VERSION_TIMESTAMP.fullmatch(version_reply.strip())
    if match is None:
        return None
    try:
        return datetime(
            year=int(match.group("year")),
            month=_MONTHS[match.group("month")],
            day=int(match.group("day")),
            hour=int(match.group("hour")),
            minute=int(match.group("minute")),
            second=int(match.group("second")),
            tzinfo=UTC,
        )
    except (KeyError, ValueError):
        return None


class ClamdDocumentSafetyScanner:
    """Bounded, fail-closed ClamD adapter using the framed INSTREAM protocol."""

    def __init__(
        self,
        *,
        host: str = CLAMD_DEFAULT_HOST,
        port: int = CLAMD_DEFAULT_PORT,
        connect_timeout_seconds: int = 2,
        scan_timeout_seconds: int = 60,
        max_stream_bytes: int = 10 * 1024 * 1024,
        signature_max_age_seconds: int = 24 * 60 * 60,
        clock_skew_tolerance_seconds: int = 5 * 60,
        now: Callable[[], datetime] | None = None,
        connection_factory: Callable[[tuple[str, int], float], _Socket] | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._connect_timeout_seconds = connect_timeout_seconds
        self._scan_timeout_seconds = scan_timeout_seconds
        self._max_stream_bytes = max_stream_bytes
        self._signature_max_age = timedelta(seconds=signature_max_age_seconds)
        self._clock_skew_tolerance = timedelta(seconds=clock_skew_tolerance_seconds)
        self._now = now or (lambda: datetime.now(UTC))
        self._connection_factory = connection_factory or socket.create_connection

    def scan(self, source: BinaryIO) -> DocumentScanResult:
        if not self.is_healthy():
            return DocumentScanResult.UNAVAILABLE
        try:
            reply = self._scan_stream(source)
        except (OSError, TimeoutError, ValueError):
            return DocumentScanResult.UNAVAILABLE
        if reply == "stream: OK":
            return DocumentScanResult.CLEAN
        if reply.startswith("stream: ") and reply.endswith(" FOUND"):
            signature = reply[len("stream: ") : -len(" FOUND")]
            if signature and "\x00" not in signature:
                return DocumentScanResult.INFECTED
        return DocumentScanResult.UNAVAILABLE

    def is_healthy(self) -> bool:
        """Require a responsive daemon with recently loaded definitions."""

        try:
            if self._command("PING") != "PONG":
                return False
            signature_timestamp = parse_clamd_database_timestamp(
                self._command("VERSION")
            )
        except (OSError, TimeoutError, ValueError):
            return False
        if signature_timestamp is None:
            return False
        now = self._now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        else:
            now = now.astimezone(UTC)
        if signature_timestamp > now + self._clock_skew_tolerance:
            return False
        age = max(timedelta(0), now - signature_timestamp)
        return age <= self._signature_max_age

    def _command(self, command: str) -> str:
        deadline = monotonic() + self._connect_timeout_seconds
        with self._connect(deadline) as connection:
            self._set_remaining_timeout(connection, deadline)
            connection.sendall(b"z" + command.encode("ascii") + b"\0")
            return self._receive_record(connection, deadline)

    def _scan_stream(self, source: BinaryIO) -> str:
        deadline = monotonic() + self._scan_timeout_seconds
        with self._connect(deadline) as connection:
            self._set_remaining_timeout(connection, deadline)
            connection.sendall(b"zINSTREAM\0")
            streamed_bytes = 0
            while chunk := source.read(CLAMD_STREAM_CHUNK_BYTES):
                streamed_bytes += len(chunk)
                if streamed_bytes > self._max_stream_bytes:
                    raise ValueError("document exceeds the bounded scanner stream")
                self._set_remaining_timeout(connection, deadline)
                connection.sendall(pack(">I", len(chunk)) + chunk)
            self._set_remaining_timeout(connection, deadline)
            connection.sendall(pack(">I", 0))
            return self._receive_record(connection, deadline)

    def _connect(self, deadline: float) -> _Socket:
        remaining = self._remaining_seconds(deadline)
        return cast(
            _Socket,
            self._connection_factory(
                (self._host, self._port),
                min(float(self._connect_timeout_seconds), remaining),
            ),
        )

    @staticmethod
    def _remaining_seconds(deadline: float) -> float:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError("ClamD operation deadline exceeded")
        return remaining

    def _set_remaining_timeout(self, connection: _Socket, deadline: float) -> None:
        connection.settimeout(self._remaining_seconds(deadline))

    def _receive_record(self, connection: _Socket, deadline: float) -> str:
        reply = bytearray()
        while True:
            self._set_remaining_timeout(connection, deadline)
            chunk = connection.recv(1024)
            if not chunk:
                break
            terminator = chunk.find(b"\0")
            if terminator >= 0:
                reply.extend(chunk[:terminator])
                break
            reply.extend(chunk)
            if len(reply) > CLAMD_MAX_REPLY_BYTES:
                raise ValueError("ClamD reply exceeded the bounded record size")
        if not reply or len(reply) > CLAMD_MAX_REPLY_BYTES:
            raise ValueError("ClamD returned an empty or oversized reply")
        try:
            return bytes(reply).decode("utf-8", errors="strict").strip()
        except UnicodeDecodeError as exc:
            raise ValueError("ClamD returned a malformed reply") from exc
