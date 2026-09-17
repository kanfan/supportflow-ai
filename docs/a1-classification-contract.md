# A1: ticket classification contract

Status: Proposed for Eray's review. Date: 2026-09-17.
Implementation lead: Emir. Reviewer: Eray.

R1 closed in #56 after #58 merged as `a7cd892` and main CI passed. Emir has
selected the original AI/RAG direction in response to Eray's proposal. A1 is
the next bounded package; Kafka R2-R4 and live AWS remain deferred. This
document proposes concrete defaults, not implemented AI capability.

## Delivery and ownership

1. A1a: provider-neutral schemas, deterministic input preparation, fake adapter,
   synthetic labelled fixtures and evaluation harness. No migration, public
   endpoint, credential or external request. Emir implements; Eray reviews.
2. A1b: tenant-scoped application integration and real provider evaluation,
   each in a reviewable PR. Before implementation, review persistence/API/job
   design, model choice, prices, credentials and an explicit run budget.
3. A2 retrieval is proposed for Eray to implement with Emir reviewing; A3
   cited drafts/human review is proposed for Emir with Eray reviewing. These
   later assignments and estimates require joint confirmation.

A1a estimate: 2-3 focused development sessions plus review, provisionally;
re-estimate after the first PR. A1b/A2/A3 dates are unset until their contracts
and provider dependencies are known. Do not silently extend the old weekly dates.

## Classification purpose and schema

Suggest one ticket category to an agent. Do not change ticket status, assign
an agent, set urgency or send a reply automatically. Classification is separate
from retrieval and answer generation.

Proposed schema version: `ticket_classification.v1`. Output has exactly:

- `outcome`: `classified` or `insufficient_context`;
- `category`: `account_access`, `billing`, `technical_issue`, `how_to`,
  `feature_request`, or `other`; null only for insufficient context.

Reject extra fields, unknown labels, invalid combinations and malformed JSON.
No free-form rationale or model confidence number in this first contract.
`other` means an identifiable request outside the taxonomy; insufficient
context means there is not enough information to identify a request.
Provider failure is an operational error, never an insufficient-context answer.

Fixtures must define each category with examples and tie-breaking rules before
the schema is frozen. Use primary requested resolution for mixed requests;
unresolvable ambiguity should abstain. Confirm these defaults during review.

## Input and freshness

A1 classifies the opening request: ticket subject and the earliest non-system
message, ordered by `(created_at, id)`. Include agent-authored opening messages
because the current API supports agents recording customer requests. Follow-up
conversation classification is a separate future policy.

The application loads inputs through verified tenant context; clients cannot
supply another tenant's classification context. The provider receives only
subject/body, schema instructions and taxonomy, not user/customer identifiers,
email fields, access tokens or organization credentials. For this milestone,
external evaluations use explicitly synthetic fixtures only.

Proposed bounds: subject 300 characters, opening body 8,000 characters. Reject
oversize input with a typed error; do not silently truncate. Missing eligible
message/empty useful content yields insufficient context without a provider call.
Treat ticket text as untrusted data, including instructions embedded in it.

Canonical input identity includes the exact selected text, message identity,
input-policy version and taxonomy version. A1a tests deterministic fingerprinting;
fingerprints are internal and not log fields. A1b must preserve the input identity,
provider/model and prompt/schema versions with the result. Changes to those
inputs invalidate reuse; an old result must never be presented as current.
Later messages alone do not change this explicitly opening-request policy.

## Provider and error boundary

A1a defines a small classification interface and typed timeout, unavailable,
rate-limited, invalid-output and input-too-large errors. Fake outcomes are
scripted by fixture ID, never used as model-quality evidence. Normal tests
require no secret or network access. Fake mode must be labelled explicitly.

For A1b, propose a 30-second total deadline and at most two attempts within that
deadline for transient failures only. No retry for invalid schema, oversize
input or authentication failure. Real adapter behavior and retry-after handling
must be verified against selected provider documentation before adoption.
Never log input bodies, raw provider responses, prompts or credentials. Record
only allowlisted operational categories, elapsed time and usage where available.

Do not reuse the document-ingestion outbox as a generic AI-job queue without a
separate design. External calls cannot participate in a DB transaction; repeated
calls may incur cost even if persisted results are deduplicated. A1b must define
this boundary before adding asynchronous classification.

## Evaluation and acceptance

Prepare a versioned corpus of at least 48 synthetic cases: six per category and
12 insufficient-context cases. Include Turkish/English, mixed intent, malformed
input, embedded instructions and oversized input coverage in contract tests.
Both contributors review labels; split development and held-out cases before
prompt tuning. Keep case IDs and expected outputs stable.

A1a acceptance: strict schema/error tests, deterministic input selection and
fingerprinting, no external requests, reproducible machine-readable evaluation
report, and a documented run command. Fake outputs test reporting plumbing only;
they cannot establish accuracy. Tenant isolation/stale-result DB tests belong
to A1b when the actual persistence/API paths exist.

A1b reports per-class precision/recall, macro-F1, confusion matrix, abstention
coverage/correctness, schema failures, latency and actual token/cost usage.
Report failures in denominators. Review quality thresholds before the first
held-out real-model evaluation; no threshold is selected after seeing scores.
No paid run begins until model, maximum requests/tokens and spend cap are recorded.

## Completion

A1 closes only when its agreed application slice and real-provider evidence
are reviewed, or the team explicitly records a reduced fake-only deliverable
without AI-quality claims. A1a alone does not complete AI classification.
Then refine A2's chunk/version/retrieval contract, and later A3's citation,
insufficient-context and human-review behavior.
