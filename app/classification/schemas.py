import json
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from app.classification.errors import InvalidProviderOutputError


SCHEMA_VERSION = "ticket_classification.v1"
Category = Literal[
    "account_access", "billing", "technical_issue", "how_to", "feature_request", "other"
]


class ClassificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    outcome: Literal["classified", "insufficient_context"]
    category: Category | None

    @model_validator(mode="after")
    def category_matches_outcome(self) -> Self:
        if (self.outcome == "classified") != (self.category is not None):
            raise ValueError("Category must match outcome")
        return self


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def parse_provider_output(raw: str) -> ClassificationResult:
    # Bound parsing work and discard provider text/validation details at this port.
    try:
        if not isinstance(raw, str) or len(raw) > 4096:
            raise ValueError("Invalid response")
        value = json.loads(raw, object_pairs_hook=_unique_object)
        return ClassificationResult.model_validate(value)
    except (ValueError, TypeError, RecursionError, ValidationError):
        raise InvalidProviderOutputError from None
