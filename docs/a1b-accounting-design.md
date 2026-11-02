# A1b accounting, transport and reporting design

Status: PROPOSED for review, 2026-11-02. No implementation or run approval.
Depends on the [provider/evaluation proposal](./a1b-provider-evaluation-proposal.md).
PR #66 merged as `2ea5225` as a proposed document only. #59 remains open.
Effective external-call/spend allowances remain **0 / USD 0**. This PR contains
no migration, SDK install, credential read, model call or real-provider code.

## Decisions and owners

| Gate | State / owner |
| --- | --- |
| Account/project/key owner | Eray, designated by Emir; Eray's confirmation/access pending |
| Implementation and evidence | Emir implements; Eray reviews |
| Model/snapshot | Reopened: prior GPT-4.1 mini candidate deprecated; no replacement selected |
| Budget and thresholds | Prior USD 1 / token / call ceilings and quality thresholds remain proposals |
| Exact SDK + transport pins | Unselected; review lockfile versions/hashes and retry/deadline controls before implementation |
| Whole-request token bound | Unproven; pin tokenizer/protocol accounting and evidence before implementation |
| Migration/ledger design | Proposed below; not executable authorization |
| Paid evaluation | Requires a separate dated, exact-manifest approval after implementation review |

The pinned candidate's published shutdown date is 2027-01-14. Preserve the old
proposal rather than silently adopting the provider's suggested replacement.
Recheck model lifecycle/access and pricing again before any approved run.
[Deprecation source, checked 2026-11-02](https://developers.openai.com/api/docs/deprecations).

## Boundary: synthetic evaluation, not customer-ticket activation

Propose separate tenant-scoped evaluation tables and a manual evaluation runner.
Reuse pure A1 input preparation/schema validation, not the fixture-ID fake as a
real classifier. Do not alter `classification_operations`' fake-only/unknown-usage
constraints or enable real ticket API routes. Real-ticket rollout remains separate.
This separation is proposed for review, not a claim that a runner already exists.

Before a network-capable implementation is approved, attach exact SDK/transport
version evidence (including retry defaults), model support, immutable request
schema, tokenizer/version and a conservative framing/schema overhead proof.
No estimate based only on character count is sufficient. The whole request must
fit the approved billable-input bound; otherwise fail locally without dispatch.
A remote token-count request is itself an external call needing an explicit
allowance; it is not a free implicit preflight step.

## Proposed schema and invariants

Names are design labels, not migrations committed by this PR.

- `evaluation_campaigns`: organization + campaign IDs, designated owner membership,
  lifecycle state, immutable approved ceilings, currency/price version, manifest
  digest, and aggregate consumed/reserved counters. One approved synthetic tenant.
- `evaluation_runs`: organization/campaign/run IDs, phase and pass ordinal, immutable
  corpus/prompt/model/config/SDK/token-proof hashes, deadline policy and approval
  reference. Bind each run to its campaign with composite tenant keys.
- `evaluation_cases`: organization/run/case IDs, expected source (`local_shortcut`
  or `provider_eligible`), safe outcome/error, dispatch state and versioned usage
  evidence. Unique `(organization_id, run_id, case_id)` prevents restart replay.
- `evaluation_reservations`: one per provider-eligible case/attempt identity, requested
  tier/model/price identity, worst-case input/output/charge, state and timestamps.
  Retain returned tier, usage fields and safe request ID when available; no text.
- `evaluation_ledger_entries`: append-only reserve/settle/reconcile events with
  unique idempotency key, tenant-scoped parent FKs, safe reason and actor membership.

Every child relation includes organization identity, not just a global UUID.
Actor FKs bind `(organization_id, actor_user_id)` to membership, with active-user,
organization, membership and role rechecks at request and completion boundaries.
Do not store prompts, fixture bodies, raw responses, keys or provider exceptions.
Store nullable usage separately from calculated cost; null never means zero.

Use exact Decimal arithmetic and integer nano-USD ledger units, rounding each
worst-case reservation upward. Freeze the price schedule, currency and supported
billable token categories. Constraints prohibit negative counters and duplicate
settlement. Refunds/reconciliation are explicit compensating entries, not edits
to historical ledger entries. Measured usage can exceed an estimate: record the
full liability, mark an overrun and stop, rather than rejecting evidence because
it exceeds a cap. Tests must prove projections reconcile with the immutable ledger.

Do not automatically reset budgets on date change, restart, new prompt or new run.
Development and held-out caps are sublimits of the same campaign/tenant cap.
A run record cannot enlarge the parent ceilings. Unknown liability remains counted.

## Reservation, dispatch and completion

Use a single lock order everywhere: campaign, phase budget (fixed order), run,
case, reservation. All accounting scopes are updated in one DB transaction.

1. Validate the exact run approval and unexpired manifest, tenant/actor, immutable
   configuration, model lifecycle, price identity and whole-request token bound.
   Derive shortcut classification locally. A shortcut writes its result/audit but
   no provider reservation, generation attempt or provider-token measurement.
2. For a provider-eligible case, lock accounting rows, check the one-in-flight rule
   and remaining phase/tenant/campaign token/call/money limits. Atomically create
   the unique case/attempt, reserve all worst-case liability, and commit a durable
   dispatch-intent marker. No approval or insufficient capacity means no marker
   and no network request. Rollback leaves all counters and claims unchanged.
3. Release the transaction and DB connection. Only the process that created the
   dispatch intent may make the one transport call. SDK and transport retries are
   disabled. Duplicate requests and runner restarts never reclaim dispatch rights.
4. Reopen a transaction, revalidate run/config/access, and settle valid reported
   usage, result/error and audit atomically. Charge known usage even on refusal,
   invalid output or revoked access; withhold the suggestion if authorization or
   input/config changed. Verify requested and returned model/tier before using
   the approved price schedule. Settlement idempotency prevents duplicate charging.

A crash after the intent commit is ambiguous even if it happened just before
the network call. Retain the full reservation and mark/project `unknown`; no
automatic redispatch. A client timeout cannot cancel assumed provider liability.
Stop the campaign on unknown usage, unrecognized billing categories, unexpected
tier/model or malformed usage. The in-flight slot remains blocked until reviewed
reconciliation. On a tier mismatch, the old price bound may no longer apply:
record the unresolved exposure and stop; do not claim an enforced cost guarantee.
Cancellation before dispatch requires proof that no intent was committed; after
intent, use the same conservative unknown/reconciliation path.

Eray supplies sanitized usage/billing evidence for operator reconciliation; Emir
reviews the exact reservation and compensating entry. Neither elapsed time nor a
missing dashboard charge proves no billable work occurred. The reconciliation
procedure and any additional provider lookup requests need separate approval.
The existing public ticket routes get no force/retry or budget-increase endpoint.

## Transport contract and evidence

Explicitly send `service_tier="default"`, not omitted/`auto`. Official reference
states that `auto` follows project settings and that the returned tier may differ
from the requested tier. Assert the outbound field in the mocked request bytes
and validate the returned value before price-based settlement.
[Responses reference, checked 2026-11-02](https://developers.openai.com/api/reference/python/resources/responses/methods/create).

Also verify pinned model, output bound, strict schema, no tools/history/background
or streaming, `store=false`, and zero retries at every layer. Treat refusal,
truncated/invalid output and absent usage explicitly. Redact provider text and
credentials from errors/logs. A proposed 20-second transport deadline must fit
the 30-second end-to-end case ceiling including reservation and persistence; use
remaining-deadline checks/DB lock timeouts rather than independent additive waits.
No successful acceptance if any case exceeded its agreed deadline or has unknown
usage. Deadlines and token caps must be re-approved if the candidate model changes.

## Reporting: system versus provider evidence

The fixed held-out corpus contains 16 cases. Existing input preparation selects
`insufficient_context-11` and `insufficient_context-12` as local missing-opening
shortcuts. Thus 14 cases are provider-eligible, not necessarily 14 actual calls.
This was verified with the existing fake evaluator on 2026-11-02, without a real
provider. No fixture labels, content or split allocation were changed.

- `system`: denominator 16, including both shortcuts, provider failures and any
  not-run cases after a stop. Report complete supports and failures; never drop
  cases to make a partial run look complete. Predeclared quality thresholds from
  the parent proposal apply here unless jointly revised before evaluation.
- `local_shortcut`: denominator 2; report shortcut count/outcomes separately, with
  zero dispatches and no model-latency/token claims. These abstentions do not count
  as model-only successes.
- `provider_eligible`: denominator 14, including preflight-blocked/not-run/error
  cases. Report per-class/confusion/abstention metrics and call counts separately.
  An attempted-only diagnostic may be additional but must state its denominator;
  never substitute it for the declared 14-case slice or 16-case system view.
- `transport`: actual dispatch-attempt count, known/unknown usage and latency
  sample counts. Not-run cases are not provider errors, but are explicit failures
  of evaluation completeness. Missing/unknown measurements stay null, not zero.

The 16-call held-out and 112-call campaign ceilings remain conservative proposals,
not targets. The two shortcut slots cannot be reassigned to extra model samples.
No automatic retry or repeated held-out scoring. Model-only metrics are descriptive
until separate model-only thresholds are approved; do not silently transfer the
system thresholds or claim model quality from deterministic shortcut behavior.
Acceptance requires a complete report, no unknown usage, all deadline/cost gates
and both contributors' review, not merely passing the system macro-F1 threshold.

## Migration and test acceptance before a run

Propose additive tables only, zero seeded campaign authorization and no rewrite of
fake history. Downgrade refuses while reservations/ledger/evidence remain; reviewed
archival is separate. Empty-schema upgrade/downgrade and metadata parity must pass.

Required mock/offline and PostgreSQL tests: tenant/actor FKs, explicit service tier
and returned-tier mismatch, zero retries on all error paths, whole-request bound
rejection before dispatch, concurrent exhaustion of each sublimit, same-case replay,
crash before/after intent, unknown slot retention, duplicate settlement, overrun
recording, authorization revocation, safe reporting and post-flush rollback of
result/audit/ledger updates. Assert no checked-out DB connection across transport.
Test 16/2/14 denominators, partial-run stops and exclusion of shortcut abstentions
from provider metrics. Normal CI remains network-free with respect to providers.

Next gate: review this design and resolve model lifecycle, exact SDK/transport pins,
token-bound evidence, owner confirmation and numeric approvals. Only then request
explicit mock-only implementation approval. A later exact-run approval is still
required before any key access or external API request. #59 remains open.
