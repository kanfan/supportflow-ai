# A1a synthetic fixtures and offline evaluation

Issue #59, second offline slice. Emir implements; Eray reviews.
Label review: **pending Eray's review**. No model-quality acceptance claimed.

## Corpus and split

`tests/fixtures/classification/corpus.v1.json` contains 48 researcher-authored
synthetic cases, never customer tickets or human-interview transcripts. Each
case records a stable ID, language, subject/opening body, expected outcome,
rationale and split. There are six cases per category (three English and three
Turkish), plus 12 insufficient-context cases. Per category, IDs 01-04 are
development and 05-06 held out; insufficient-context uses 01-08 and 09-12.
The split is fixed before any model/prompt tuning: 32 development, 16 held out.

Review labels and rationales before real evaluation, especially billing/access
precedence, mixed primary requests, absent features versus broken features,
and ambiguous short requests. The corpus is deliberately small; it cannot
support broad commercial accuracy claims. Held-out labels are visible in this
repository for joint review, not a secret independent benchmark. Do not tune a
future prompt on them; if exposed during tuning, create a fresh held-out set.
Label/split/content changes require a reviewed corpus revision, not silent edits
to an earlier evidence artifact. Reports fingerprint the actual fixture content.

## Scripted predictions and metric definitions

`fake-scripts.v1.json` holds independent scripted responses keyed by case ID.
The evaluator never derives predictions from expected labels. It deliberately
misclassifies `account_access-01` and `billing-05`, injects a timeout at
`technical_issue-02`, and authentication failure at `how_to-06`. Four empty or
missing-opening cases bypass the fake under the input contract.

Expected plumbing totals are development 30/32 correct with one error, held-out
14/16 with one error, and all 44/48 with two errors. These are programmed test
outcomes, not model performance. Changing a label does not change the script.

- Accuracy denominator includes every selected case, including provider errors.
- Confusion matrix rows are expected categories/abstention; columns also include
  `error`. A failed labelled request remains a false negative for that category.
- Per-class precision/recall/F1 include integer supports and prediction counts.
  Undefined divisions are null. Category macro-F1 covers the six named categories,
  using zero for an undefined category F1; abstention is reported separately.
- Classification coverage is classified/total. Abstention coverage is
  abstained/total, precision is correct abstentions/all abstentions, and recall
  is correct abstentions/expected insufficient-context cases.
- Latency, token usage and cost are null because this fake run measures none of
  those real-provider properties. A1b must add real measurements and pre-agreed
  quality thresholds before evaluating a model.

## Reproduction

Run from the repository root, with no credentials:

```powershell
uv run python -m scripts.evaluate_classification
uv run python -m scripts.evaluate_classification --split held_out
uv run python -m scripts.evaluate_classification --split all --output tmp/a1a-fake-all.json
uv run pytest tests/test_classification_evaluation.py tests/test_classification_foundation.py -q
```

Default split is development. The CLI only loads the committed fake corpus and
scripts. An output file is created exclusively: an existing evidence file is
never overwritten. Create the output parent directory first if needed. Reports
contain case IDs, expected/predicted labels and safe error codes, no ticket text,
rationale, raw provider response or credential. Identical fixtures and split
produce identical JSON; semantic corpus/script hashes and policy versions make
changes traceable. A successful CLI exit means the plumbing ran, not a quality
threshold was reached; intentional wrong answers are expected.

## Build and verification

`app/classification/evaluation.py` validates the v1 allocation and script coverage,
executes the existing input/service boundary and calculates the report.
`scripts/evaluate_classification.py` supplies the CLI and safe file handling.
Tests check hand-calculated metrics, errors in denominators, split counts,
determinism, label/script independence, invalid fixtures and overwrite refusal.
In-process tests forbid socket connections. No provider SDK is introduced.

#59 remains open for joint label/evidence acceptance and A1b's reviewed
tenant-scoped persistence/API/job design, provider/model and budget gates.
