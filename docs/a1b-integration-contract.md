# A1b: application integration and provider gate

Status: Accepted for fake-only application integration. Updated: 2026-10-04.
Eray approved `3f09c2a` in PR #64; merged as `c8d331d`. Implementation evidence
is a separate review; provider/model, limits, budget and quality gates remain open.
Issue #59 stays open. Emir implements; Eray reviews. This document authorizes
no external request, credential setup, paid evaluation, or production rollout.

## Baseline and delivery gates

A1a defines the opening-request input, strict output, safe errors and scripted
offline evaluation. PR #63 records joint review of 48 labels and the fixed
32 development / 16 held-out split. Fake scores are not model-quality evidence.
The accepted [A1 contract](./a1-classification-contract.md) remains authoritative.

1. Approve this application design and the fake-only implementation scope before
   starting fake integration.
2. Implement migration, service and API with a deterministic test adapter in a
   separate PR. No real adapter is implemented or enabled. Review
   tenant/concurrency evidence.
3. Complete the provider approval record below, including exact model, current
   official documentation/pricing, credential handling, request/token limits and
   spend caps. Approve it before
   real-adapter implementation. Unfilled fields mean the gate is closed.
4. Implement and review the adapter with mocked transport tests, then explicitly
   authorize a bounded synthetic evaluation. Review evidence before A1 closure.

The parent contract's delivery section records this same accepted sequence of
fake-application and real-provider gates. Fake-only approval never authorizes external calls
or spend, which remain zero until the separate gates are approved. Kafka,
live AWS, retrieval, automatic replies and ticket-state changes remain excluded.

## Accepted API and authorization

Reuse `OrganizationContext` and ADMIN/AGENT roles from `app/api/tickets.py`.
The organization header is only a selector: active user, organization and
membership are checked server-side. Every ticket/result query includes the
verified organization ID; cross-tenant and missing tickets both return 404.

- `POST /api/v1/tickets/{ticket_id}/classification`: explicit on-demand request,
  no client subject/body, organization override, provider or model selection.
  Return 200 with a fresh result (new or reused); no automatic ticket mutation.
- `GET /api/v1/tickets/{ticket_id}/classification`: read-only; return a typed
  state `not_requested`, `current`, `stale`, `in_progress`, `failed` or `unknown`.
  Only `current` includes the category/outcome suggestion. Never call a provider.
- Keep operational status separate from the strict two-field model result.
  Include mode (`fake`/`real`), result ID, completion time and configuration
  version in the application envelope. Never expose fingerprints or raw output.
- Proposed POST failures: 409 for an active/uncertain operation or changed input,
  422 for oversized input, 503 for disabled mode, exhausted budget, provider
  authentication/unavailability/rate limit, 504 for deadline, 502 for invalid
  output. Use safe machine codes; never forward provider text or credentials.
  Client retry guidance must not imply an uncertain call is free to repeat.

The fake integration is for isolated tests/local synthetic demonstrations only.
The existing fixture-ID fake is not a classifier for arbitrary ticket IDs; use
dependency injection with explicit test scripts, never map production tickets
to fixture labels. Default classification mode is disabled, and real mode is
unavailable until its separate gate. No UI work is required for this slice.

## Accepted persistence and freshness

Add classification operations/results with a composite foreign key
`(organization_id, ticket_id)` to the existing tenant-scoped ticket key.
Also require a non-null `requesting_actor_user_id` and composite foreign key
`(organization_id, requesting_actor_user_id)` referencing
`organization_members(organization_id, user_id)`. Resolve the actor from verified
server-side membership, never a client-supplied user ID. A user existing only in
another organization cannot be recorded as the requesting actor. This FK proves
membership identity, not active status or role: retain runtime active-user,
organization, membership and role checks, including the completion recheck.
Store:

- operation UUID, tenant/ticket IDs, `requesting_actor_user_id` and timestamps;
- selected opening message identity and exact A1a input fingerprint;
- input-policy, taxonomy, schema, prompt, adapter, provider and pinned model
  identities; a server-owned configuration digest covering generation settings;
- operational state, attempt count, safe error code and validated result fields;
- elapsed duration, token usage and cost accounting (null when unknown, not zero).

Do not duplicate subject/body, prompt text or raw response in result/audit rows.
Database constraints enforce valid result pairs and no result for failed or
uncertain operations. A unique active claim for each tenant/ticket/input/config
identity prevents simultaneous duplicate calls; a matching successful result is
reused. Preserve historical attempts without treating them as current.

Recompute input/config identity for every read and before publishing a result.
Subject/opening-message changes or any configuration/version change invalidate
reuse; later messages alone do not. Use the existing earliest non-system
`(created_at UTC, id)` selection, limits and local abstention shortcuts unchanged.
An old row remains historical, never silently relabelled as current. A stale
completion returns 409; an explicit later request may classify the new input.

## Execution and transaction boundary

Use synchronous, bounded on-demand execution for the first slice. No Celery
classification job and no document-ingestion outbox reuse. Queued execution is
a separate review if request duration/load later requires it.

1. In a short transaction, revalidate authorization, lock the scoped ticket,
   select the opening input, reuse a current result or atomically claim work.
   For real mode also reserve request/token/cost capacity atomically. Commit.
2. Close the transaction and release the DB connection before external I/O.
   Carry only an immutable input snapshot and identifiers, not lazy ORM objects.
   The dependency session may already have an auth transaction: end it explicitly.
3. Perform the local shortcut or provider operation outside any DB transaction.
4. In a new transaction, lock the ticket/claim, recheck authorization and identity,
   validate the result, settle known usage, and persist completion plus a safe
   audit event atomically. Do not publish if access was revoked or input changed.

Ticket/message writers that can affect opening-input selection must acquire the
same ticket lock so the final identity check and save cannot race with writes.
Test that the existing append path participates, including the no-message case.
Read freshness describes the checked snapshot, not a promise against later edits.

An active duplicate request receives 409 and can inspect GET; it does not make
another call. A crash, disconnect or ambiguous transport failure after dispatch
can leave cost and completion unknown. Expiry changes a claim to `unknown`, not
automatically retryable. No background re-dispatch. Reconcile manually with
provider usage before release/retry; retain the reservation if usage is unknown.
Expose this reconciliation as an operator procedure, not a public force endpoint.
Persisted deduplication does not guarantee exactly-once billing.

## Provider, retry and budget approval record

The initial approved spend is **0** and allowed external requests are **0**.
No default model, price or implicit trial credit is assumed. Before gate 3,
record and jointly approve all of the following in a dated follow-up:

| Required decision | Current value |
| --- | --- |
| Provider, pinned model/version, SDK/version | Unselected; blocked |
| Official schema, timeout/retry, usage and data-policy references | Not yet verified |
| Price currency, per-token rates, effective date, billable token types | Unselected |
| Credential owner and secret injection/rotation procedure | Unassigned; never commit secrets |
| Input/output token limits and tokenizer/accounting method | Unset; character limits alone are insufficient |
| Maximum attempts, development requests and held-out requests | Unset; external calls disabled |
| Per-run, tenant and aggregate spend caps and concurrency | Unset; effective cap 0 |
| Synthetic dataset/config hashes, run owner and explicit run approval | Required before each paid run |

Retain the parent proposal of a 30-second total deadline and at most two attempts
only as a ceiling pending provider verification. Disable hidden SDK retries.
Authentication, invalid schema and oversize errors never retry. Transient retries
require documented safe behavior, remaining deadline and a separate reservation
for each billable attempt; uncertain dispatch is not automatically retried.
Honor retry-after only if it fits the remaining deadline and approved limits.

Budget checks must reserve worst-case charge before dispatch, atomically across
concurrent requests. Include all provider-billable token types and every attempt.
No reliable upper bound means no call. Settle actual usage where available; keep
unknown usage reserved. A billing dashboard alert alone is not a hard cap.
Report measured usage separately from calculated cost, with its pricing version;
do not describe a calculated estimate as a settled invoice amount.

Real evaluations use synthetic fixtures only. Enabling real mode on customer
tickets requires separate data-policy, retention and deployment approval; it is
not implied by successful evaluation. Provider input remains only subject/body
plus versioned instructions, never tenant/user/customer IDs or email fields.

## Proposed quality gates (not yet accepted)

Before the first held-out run, jointly agree or revise these proposed thresholds:
six-category macro-F1 >= 0.80, abstention precision >= 0.80 and recall >= 0.75,
zero schema failures and zero provider errors in the 16-case held-out report.
Failures remain in denominators; report per-class support/precision/recall/F1,
confusion matrix, accuracy and classified/abstained coverage without optimizing
coverage at the expense of correct abstention. Retain per-case safe outcomes.

Proposed latency gate: every completed request fits the agreed total deadline;
report sample count, median, p95 and maximum rather than claiming an SLA from
16 cases. Cost gate: stay within the explicitly approved request/token/spend
caps, with no unknown usage at acceptance. These are small-corpus engineering
gates, not evidence of general customer accuracy or commercial readiness.

Tune on development only; freeze prompt/model/settings and record hashes before
held-out evaluation. Any changes following held-out inspection require a fresh,
jointly labelled held-out set. Do not repeatedly evaluate the same set until it
passes. A budgeted evaluation failure is evidence to review, not authorization
for additional calls. Keep real reports distinct from scripted fake reports.

## Required implementation evidence and review questions

- Tenant isolation for GET/POST, inactive membership/organization, invalid roles,
  missing tickets, and access revoked during execution.
- Composite-FK/result constraints, migration upgrade/downgrade, stale input and
  configuration, deterministic ties, local shortcuts and unchanged later messages.
- A database integration test must reject inserting a classification operation
  for tenant A with a requesting actor who has membership only in tenant B, even
  when the user and tenant A ticket both exist. Include a same-tenant membership
  success case; runtime tests separately reject inactive or unauthorized members.
- Concurrent requests make one fake call; external I/O holds no DB transaction;
  stale completion cannot publish; process loss stays unknown without re-dispatch.
- Atomic budget reservation under concurrency, retry ceilings/deadlines, unknown
  usage, disabled mode, and no secrets/text in responses, logs or audit metadata.
- Normal tests remain offline; existing CI and container smoke remain green.

Synchronous-first scope, claim/unknown reconciliation, API/error envelope,
locking/freshness and the split approval gates were accepted in PR #64. Separately
agree provider/model, token/request/spend caps and the proposed quality thresholds
before real-adapter work. #59 closes only after both contributors review the
agreed application slice and real-provider evidence (or explicitly reduce scope).
