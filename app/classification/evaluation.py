"""Offline, scripted evaluation plumbing; contains no real provider adapter."""

from collections import Counter
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
from typing import Literal, get_args
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.classification.errors import (
    ClassificationError,
    InputTooLargeError,
    InvalidProviderOutputError,
    ProviderAuthenticationError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.classification.inputs import (
    INPUT_POLICY_VERSION,
    TAXONOMY_VERSION,
    OpeningMessage,
)
from app.classification.providers import FakeClassificationProvider
from app.classification.schemas import SCHEMA_VERSION, Category, ClassificationResult
from app.classification.service import classify_opening_request


Split = Literal["development", "held_out", "all"]
ERRORS: dict[str, type[ClassificationError]] = {
    error.category: error
    for error in (
        InputTooLargeError,
        InvalidProviderOutputError,
        ProviderAuthenticationError,
        ProviderRateLimitedError,
        ProviderTimeoutError,
        ProviderUnavailableError,
    )
}


class FixtureModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class EvaluationCase(FixtureModel):
    id: str = Field(pattern=r"^[a-z_]+-[0-9]{2}$")
    split: Literal["development", "held_out"]
    language: Literal["en", "tr"]
    subject: str = Field(max_length=300, repr=False)
    body: str | None = Field(max_length=8000, repr=False)
    expected: ClassificationResult
    rationale: str = Field(min_length=1, repr=False)


class Corpus(FixtureModel):
    version: Literal["classification_corpus.v1"]
    label_review: Literal["pending_eray_review", "jointly_reviewed"]
    cases: list[EvaluationCase]


class ScriptCase(FixtureModel):
    id: str
    response: str | None = Field(default=None, repr=False)
    error: str | None = None

    @model_validator(mode="after")
    def exactly_one_script(self):
        if (self.response is None) == (self.error is None):
            raise ValueError("Exactly one scripted response or error required")
        if self.error is not None and self.error not in ERRORS:
            raise ValueError("Unknown scripted error")
        return self


class Scripts(FixtureModel):
    version: Literal["classification_fake_scripts.v1"]
    mode: Literal["fake"]
    cases: list[ScriptCase]


def label(result: ClassificationResult) -> str:
    return result.category or "insufficient_context"


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def load_fixtures(corpus_path: Path, scripts_path: Path) -> tuple[Corpus, Scripts]:
    corpus = Corpus.model_validate_json(corpus_path.read_bytes())
    scripts = Scripts.model_validate_json(scripts_path.read_bytes())
    ids = [case.id for case in corpus.cases]
    script_ids = [case.id for case in scripts.cases]
    if len(ids) != len(set(ids)) or len(script_ids) != len(set(script_ids)):
        raise ValueError("Duplicate case identity")
    if set(ids) != set(script_ids):
        raise ValueError("Script identities must exactly cover the corpus")
    # Freeze v1's allocation before any real-model prompt tuning.
    for category in (*get_args(Category), "insufficient_context"):
        subset = [case for case in corpus.cases if label(case.expected) == category]
        counts = Counter(case.split for case in subset)
        expected = (
            {"development": 8, "held_out": 4}
            if category == "insufficient_context"
            else {"development": 4, "held_out": 2}
        )
        if counts != expected or {case.language for case in subset} != {"en", "tr"}:
            raise ValueError("Corpus v1 allocation or language coverage changed")
    return corpus, scripts


def evaluate(corpus: Corpus, scripts: Scripts, *, split: Split = "development") -> dict:
    if split not in ("development", "held_out", "all"):
        raise ValueError("Unknown evaluation split")
    selected = [case for case in corpus.cases if split == "all" or case.split == split]
    if not selected:
        raise ValueError("Selected evaluation split is empty")
    responses: dict[str, str | type[ClassificationError]] = {}
    for script in scripts.cases:
        if script.id in responses:
            raise ValueError("Duplicate scripted identity")
        responses[script.id] = (
            script.response
            if script.response is not None
            else ERRORS[script.error or ""]
        )
    rows = []
    for case in sorted(selected, key=lambda c: c.id):
        provider = FakeClassificationProvider(case.id, responses)
        messages = (
            []
            if case.body is None
            else [
                OpeningMessage(
                    uuid5(NAMESPACE_URL, "supportflow-synthetic:" + case.id),
                    datetime(2026, 9, 30, tzinfo=UTC),
                    "agent",
                    case.body,
                )
            ]
        )
        error = None
        try:
            predicted = label(
                classify_opening_request(case.subject, messages, provider)
            )
        except ClassificationError as exc:
            predicted, error = "error", exc.category
        expected = label(case.expected)
        rows.append(
            {
                "id": case.id,
                "split": case.split,
                "expected": expected,
                "predicted": predicted,
                "error": error,
                "correct": expected == predicted,
                "provider_calls": provider.calls,
            }
        )
    categories = (*get_args(Category), "insufficient_context")
    matrix = {
        expected: {predicted: 0 for predicted in (*categories, "error")}
        for expected in categories
    }
    for row in rows:
        matrix[row["expected"]][row["predicted"]] += 1
    per_class = {}
    for category in categories:
        tp = matrix[category][category]
        support = sum(matrix[category].values())
        predicted_count = sum(matrix[other][category] for other in categories)
        per_class[category] = {
            "true_positive": tp,
            "expected_count": support,
            "predicted_count": predicted_count,
            "precision": _ratio(tp, predicted_count),
            "recall": _ratio(tp, support),
            "f1": _ratio(2 * tp, support + predicted_count),
        }
    total = len(rows)
    correct = sum(row["correct"] for row in rows)
    abstained = sum(row["predicted"] == "insufficient_context" for row in rows)
    correct_abstained = matrix["insufficient_context"]["insufficient_context"]
    errors = sum(row["predicted"] == "error" for row in rows)
    classified = total - errors - abstained

    def digest(model: BaseModel) -> str:
        canonical = json.dumps(
            model.model_dump(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
        return sha256(canonical.encode()).hexdigest()

    return {
        "report_version": "classification_evaluation.v1",
        "mode": "fake",
        "quality_claim": False,
        "label_review": corpus.label_review,
        "split": split,
        "corpus_version": corpus.version,
        "corpus_sha256": digest(corpus),
        "scripts_version": scripts.version,
        "scripts_sha256": digest(scripts),
        "schema_version": SCHEMA_VERSION,
        "input_policy_version": INPUT_POLICY_VERSION,
        "taxonomy_version": TAXONOMY_VERSION,
        "total": total,
        "correct": correct,
        "errors": errors,
        "accuracy": _ratio(correct, total),
        "error_rate": _ratio(errors, total),
        "classification_coverage": _ratio(classified, total),
        "abstention": {
            "count": abstained,
            "correct": correct_abstained,
            "coverage": _ratio(abstained, total),
            "precision": _ratio(correct_abstained, abstained),
            "recall": _ratio(
                correct_abstained, sum(matrix["insufficient_context"].values())
            ),
        },
        "category_macro_f1": sum(per_class[c]["f1"] or 0.0 for c in get_args(Category))
        / len(get_args(Category)),
        "macro_f1_labels": list(get_args(Category)),
        "per_class": per_class,
        "confusion_matrix": matrix,
        "cases": rows,
        "latency_ms": None,
        "token_usage": None,
        "cost_usd": None,
        "measurement_note": "Scripted offline run; model latency, tokens and cost are not measured.",
    }
