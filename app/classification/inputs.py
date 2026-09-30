"""Pure input preparation; callers must supply already authorized ticket data."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
import json
from typing import Literal
from uuid import UUID

from app.classification.errors import InputTooLargeError
from app.classification.schemas import SCHEMA_VERSION


INPUT_POLICY_VERSION = "opening_request.v1"
TAXONOMY_VERSION = "support_categories.v1"


@dataclass(frozen=True)
class OpeningMessage:
    id: UUID
    created_at: datetime
    author_type: Literal["agent", "customer", "system"]
    body: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.id, UUID)
            or not isinstance(self.created_at, datetime)
            or self.created_at.utcoffset() is None
            or self.author_type not in ("agent", "customer", "system")
            or not isinstance(self.body, str)
        ):
            raise ValueError("Invalid opening message")


@dataclass(frozen=True)
class ProviderInput:
    subject: str = field(repr=False)
    body: str = field(repr=False)
    schema_version: str = SCHEMA_VERSION
    taxonomy_version: str = TAXONOMY_VERSION


@dataclass(frozen=True)
class PreparedInput:
    payload: ProviderInput
    message_id: UUID | None
    fingerprint: str = field(repr=False)
    insufficient_context: bool


def prepare_input(
    subject: str,
    messages: Sequence[OpeningMessage],
    *,
    input_policy_version: str = INPUT_POLICY_VERSION,
    taxonomy_version: str = TAXONOMY_VERSION,
) -> PreparedInput:
    if not isinstance(subject, str):
        raise ValueError("Invalid ticket subject")
    if not input_policy_version or not taxonomy_version:
        raise ValueError("Input versions must be nonempty")
    if len(subject) > 300:
        raise InputTooLargeError
    # Duplicate identities would make the tie-break ambiguous: fail explicitly.
    if len({message.id for message in messages}) != len(messages):
        raise ValueError("Duplicate message identity")
    eligible = [message for message in messages if message.author_type != "system"]
    opening = min(eligible, key=lambda m: (m.created_at, m.id.int), default=None)
    body = opening.body if opening else ""
    if len(body) > 8000:
        raise InputTooLargeError
    identity = {
        "subject": subject,
        "body": body,
        "message_id": str(opening.id) if opening else None,
        "input_policy_version": input_policy_version,
        "taxonomy_version": taxonomy_version,
        "schema_version": SCHEMA_VERSION,
    }
    canonical = json.dumps(
        identity, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return PreparedInput(
        payload=ProviderInput(subject, body, taxonomy_version=taxonomy_version),
        message_id=opening.id if opening else None,
        fingerprint=sha256(canonical.encode("utf-8")).hexdigest(),
        insufficient_context=opening is None
        or (not subject.strip() and not body.strip()),
    )
