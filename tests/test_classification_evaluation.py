from collections import Counter
import json
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from app.classification.evaluation import (
    Corpus,
    EvaluationCase,
    ScriptCase,
    Scripts,
    evaluate,
    load_fixtures,
)
from app.classification.schemas import ClassificationResult


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/classification"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("Offline evaluation attempted a socket connection")

    monkeypatch.setattr(socket.socket, "connect", fail)
    monkeypatch.setattr(socket.socket, "connect_ex", fail)
    monkeypatch.setattr(socket, "create_connection", fail)


def fixtures():
    return load_fixtures(FIXTURES / "corpus.v1.json", FIXTURES / "fake-scripts.v1.json")


def test_fixed_corpus_allocation_and_stable_split():
    corpus, scripts = fixtures()
    assert len(corpus.cases) == len(scripts.cases) == 48
    assert Counter(c.split for c in corpus.cases) == {"development": 32, "held_out": 16}
    assert corpus.label_review == "pending_eray_review"
    assert len({c.id for c in corpus.cases}) == 48


@pytest.mark.parametrize(
    "split,total,correct,errors",
    [("development", 32, 30, 1), ("held_out", 16, 14, 1), ("all", 48, 44, 2)],
)
def test_report_counts_intentional_mistakes_and_errors(split, total, correct, errors):
    report = evaluate(*fixtures(), split=split)
    assert (report["total"], report["correct"], report["errors"]) == (
        total,
        correct,
        errors,
    )
    assert report["accuracy"] == correct / total
    assert report["error_rate"] == errors / total
    assert (
        sum(sum(row.values()) for row in report["confusion_matrix"].values()) == total
    )
    assert report["mode"] == "fake" and report["quality_claim"] is False
    assert report["latency_ms"] is report["cost_usd"] is report["token_usage"] is None


def test_metrics_against_hand_calculated_case_set():
    expectations = ["billing", "billing", "how_to", None]
    cases = [
        EvaluationCase(
            id=f"case-{i:02d}",
            split="development",
            language="en",
            subject="private synthetic input",
            body="request",
            rationale="test only",
            expected=ClassificationResult(
                outcome="classified" if category else "insufficient_context",
                category=category,
            ),
        )
        for i, category in enumerate(expectations)
    ]
    scripts = Scripts(
        version="classification_fake_scripts.v1",
        mode="fake",
        cases=[
            ScriptCase(
                id="case-00", response='{"outcome":"classified","category":"billing"}'
            ),
            ScriptCase(id="case-01", error="provider_timeout"),
            ScriptCase(
                id="case-02", response='{"outcome":"classified","category":"billing"}'
            ),
            ScriptCase(
                id="case-03",
                response='{"outcome":"insufficient_context","category":null}',
            ),
        ],
    )
    corpus = Corpus(
        version="classification_corpus.v1",
        label_review="pending_eray_review",
        cases=cases,
    )
    report = evaluate(corpus, scripts)
    assert report["accuracy"] == 0.5 and report["errors"] == 1
    assert report["per_class"]["billing"]["precision"] == 0.5
    assert (
        report["per_class"]["billing"]["recall"] == 0.5
    )  # error stays a false negative
    assert report["per_class"]["billing"]["f1"] == 0.5
    assert report["per_class"]["how_to"]["precision"] is None
    assert report["per_class"]["how_to"]["recall"] == 0
    assert report["category_macro_f1"] == pytest.approx(0.5 / 6)
    assert report["abstention"] == {
        "count": 1,
        "correct": 1,
        "coverage": 0.25,
        "precision": 1,
        "recall": 1,
    }
    assert "private synthetic input" not in json.dumps(report)


def test_report_reproducibility_and_script_independence():
    corpus, scripts = fixtures()
    first = evaluate(corpus, scripts, split="all")
    assert first == evaluate(corpus, scripts, split="all")
    changed = corpus.model_copy(
        update={
            "cases": [
                case.model_copy(
                    update={
                        "expected": ClassificationResult(
                            outcome="classified", category="other"
                        )
                    }
                )
                if case.id == "billing-01"
                else case
                for case in corpus.cases
            ]
        }
    )
    second = evaluate(changed, scripts, split="all")
    assert [c["predicted"] for c in first["cases"]] == [
        c["predicted"] for c in second["cases"]
    ]
    assert first["corpus_sha256"] != second["corpus_sha256"]
    assert first["scripts_sha256"] == second["scripts_sha256"]
    assert first["correct"] == second["correct"] + 1
    assert all(
        c["provider_calls"] == 0
        for c in first["cases"]
        if c["id"]
        in {
            "insufficient_context-07",
            "insufficient_context-08",
            "insufficient_context-11",
            "insufficient_context-12",
        }
    )


@pytest.mark.parametrize(
    "mutation",
    ["duplicate", "missing_script", "unknown_error", "allocation", "extra_field"],
)
def test_invalid_fixture_files_fail_closed(tmp_path, mutation):
    corpus = json.loads((FIXTURES / "corpus.v1.json").read_text(encoding="utf-8"))
    scripts = json.loads(
        (FIXTURES / "fake-scripts.v1.json").read_text(encoding="utf-8")
    )
    if mutation == "duplicate":
        corpus["cases"].append(corpus["cases"][0])
    if mutation == "missing_script":
        scripts["cases"].pop()
    if mutation == "unknown_error":
        scripts["cases"][0] = {"id": "account_access-01", "error": "secret"}
    if mutation == "allocation":
        corpus["cases"][0]["split"] = "held_out"
    if mutation == "extra_field":
        corpus["cases"][0]["customer_email"] = "synthetic@example.invalid"
    a, b = tmp_path / "corpus.json", tmp_path / "scripts.json"
    a.write_text(json.dumps(corpus), encoding="utf-8")
    b.write_text(json.dumps(scripts), encoding="utf-8")
    with pytest.raises(ValueError):
        load_fixtures(a, b)


def test_cli_creates_report_and_refuses_overwrite(tmp_path):
    target = tmp_path / "report.json"
    command = [
        sys.executable,
        "-m",
        "scripts.evaluate_classification",
        "--output",
        str(target),
    ]
    run = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    before = target.read_bytes()
    assert json.loads(before)["total"] == 32
    again = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    assert again.returncode == 2 and target.read_bytes() == before
    assert "Traceback" not in again.stderr


def test_malformed_response_remains_error_in_denominator():
    corpus, scripts = fixtures()
    changed = scripts.model_copy(
        update={
            "cases": [
                ScriptCase(id=case.id, response="malformed secret response")
                if case.id == "billing-01"
                else case
                for case in scripts.cases
            ]
        }
    )
    report = evaluate(corpus, changed)
    assert report["total"] == 32 and report["errors"] == 2
    assert report["per_class"]["billing"]["recall"] == 0.75
    assert "malformed secret response" not in json.dumps(report)
