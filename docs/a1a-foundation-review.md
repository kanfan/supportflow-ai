# A1a classification foundation

Issue #59, first implementation slice. Emir implements; Eray reviews.
Reading time: approximately 3 minutes. No model-quality or completed A1 claim.

## Build and boundaries

- `app/classification/schemas.py`: exact two-field output and category/outcome
  validation; raw JSON parser rejects malformed/duplicate keys and bounds the
  response to 4,096 characters. Invalid responses become a safe typed error.
- `inputs.py`: immutable snapshots select the earliest non-system message by
  aware timestamp then UUID. Original-text character limits precede shortcuts.
  SHA-256 over canonical JSON covers exact text, selected message identity,
  input policy, taxonomy and schema versions. Changing later messages does not
  invalidate an opening-request classification. Duplicate IDs and naive times
  are rejected because they make ordering ambiguous.
- `providers.py`: a small provider protocol receives only text and schema/taxonomy
  versions. A fake binds a fixture ID at construction and returns its scripted
  response/error. It records only a call count. It cannot assess ticket meaning.
- `errors.py`: stable safe categories and retryability metadata. Authentication
  failures are non-retryable. Constructors take no raw provider message.
- `service.py`: prepare input, shortcut if the contract requires abstention,
  otherwise call once and validate the response. Unknown adapter failures become
  unavailable errors. This slice implements no retry scheduler; A1b owns real
  provider deadlines/retries and instruction rendering.

The caller must load authorized tenant data before using this pure module.
There is no route, database write, migration, worker task or provider credential.
Input fingerprints are internal identities, not authorization or privacy masks;
A1b must scope any stored result/cache to its verified tenant and ticket and
include model/prompt identity. Payload/repr suppression reduces accidental
logging, but callers must still avoid logging dataclass dictionaries or raw input.

## How to verify

```powershell
uv run pytest tests/test_classification_foundation.py -q
uv run ruff check .
uv run ruff format --check .
uv run pyright
```

The focused suite prevents socket connections. Tests cover strict result shape,
error sanitization, chronology/tie-breaking, whitespace edge cases, exact Unicode
character limits, fingerprints, scripted errors and single-call authentication
failure. These tests prove deterministic software behavior. The injected prompt
test only proves the fake ignores content; it is not evidence that a real model
resists prompt injection.

Follow-up A1a work adds at least 48 labelled synthetic cases, a reviewed split,
and machine-readable evaluation reports. A1b adds actual tenant-scoped result
integration and a separately reviewed provider/model budget. #59 stays open.
