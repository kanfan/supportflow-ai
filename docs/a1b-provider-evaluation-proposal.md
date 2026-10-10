# A1b provider and synthetic evaluation proposal

Status: PROPOSED, not approved. Prepared 2026-10-10. Emir implements; Eray reviews.
Issue #59 stays open. PR #65's fake-only integration was approved at `2bb31eb`
and merged as `bcbd5ae`. That acceptance does not authorize a real provider.
**Effective external-call allowance: 0. Effective spend allowance: USD 0.**

This document proposes decisions, not configuration changes. No SDK is installed,
credential read, account provisioned, adapter implemented or model called by this
PR. Public documentation research is not an authenticated model evaluation.

## Candidate and rationale for review

Propose OpenAI with the pinned `gpt-4.1-mini-2025-04-14` snapshot as a first
baseline for this small, two-field classification task. Its official model page
lists structured output support, a fixed snapshot and non-reasoning execution.
Published text prices checked on 2026-10-10 are USD 0.40 / million input tokens
and USD 1.60 / million output tokens. Use full input pricing in reservations,
not caching discounts. Account access, regional availability and rate limits
have not been verified. [Model and pricing](https://developers.openai.com/api/docs/models/gpt-4.1-mini).

This is an engineering starting hypothesis, not a claim that it is the latest,
best or proven accurate for Turkish support tickets. GPT-5.4 mini is a comparison
candidate, not an automatic fallback: its documented prices are USD 0.75 input
and USD 4.50 output per million tokens. Changing models requires a fresh reviewed
cost/configuration record, not a silent switch after a failed run.
[Comparison candidate](https://developers.openai.com/api/docs/models/gpt-5.4-mini).

Propose one stateless, text-only Responses request per non-shortcut case, strict
JSON output matching the existing taxonomy, no tools, browsing, files, background
mode, conversation history or streaming. Refusal and incomplete output must be
handled explicitly, never relabelled as `insufficient_context`. Structured output
does not establish semantic correctness; keep the existing local strict validator.
[Structured output guidance](https://developers.openai.com/api/docs/guides/structured-outputs).

Proposed request controls include `max_output_tokens=256`, `store=false`,
`stream=false`, `background=false` and a strict JSON schema under `text.format`;
no previous response or conversation ID. Use the standard service tier, not an
account-selected premium mode. The reference documents output-token limiting and
storage controls; their exact SDK-version behavior still needs mock verification.
[Responses reference](https://developers.openai.com/api/reference/python/resources/responses/methods/create).

## Proposed limits (inactive until approved)

| Item | Proposal |
| --- | --- |
| Input per generation | At most 8,192 billable tokens, including instructions/schema/framing |
| Output per generation | At most 256 billable tokens |
| Attempts | One per case per approved run; zero automatic retries |
| Development allowance | Up to 3 separately identified passes of 32 cases: at most 96 generations |
| Held-out allowance | One frozen pass of 16 cases: at most 16 generations |
| Aggregate campaign | At most 112 generations across all phases and restarts |
| Concurrency | One generation in flight |
| Proposed cost ceilings | USD 0.75 development, USD 0.25 held-out, USD 1.00 aggregate |
| Tenant scope | One named synthetic tenant; its cap equals the aggregate cap |
| Wall-clock ceiling | 30 seconds end-to-end per case; proposed transport deadline at most 20 seconds |

Local missing/empty-opening shortcuts consume no generation allowance, but remain
in all evaluation denominators. Limits above conservatively include them.
Do not spend unused allowance on extra tuning passes or repeated held-out runs.
No automatic fallback provider, probe, count endpoint or credential smoke call
is implicitly allowed; any such external request needs a recorded allowance.

At the candidate rates, the proposed token ceilings imply a calculated maximum
of `(8192 * 0.40 + 256 * 1.60) / 1,000,000 = USD 0.0036864` per generation,
or `112 * 0.0036864 = USD 0.4128768` for the campaign. This is a conditional
token-charge bound, not an invoice or an unconditional guarantee. It excludes
taxes, prepaid-credit purchases, currency conversion and unrelated account usage.
No credits purchase is authorized. Rate changes or extra billable categories
invalidate the calculation and require review before dispatch.

## Hard-stop design required before calls

The 8,192-token bound must be established for the entire serialized request, not
estimated from the 300/8,000 character limits. Before implementation approval,
select and pin the tokenizer/accounting method, document protocol/schema overhead,
and show why its bound is conservative for the pinned model and transport. If a
safe upper bound cannot be established, stop: neither this arithmetic nor a
dashboard alert authorizes a request. No tokenizer/version is assumed here.

Add a persistent, atomic reservation ledger before any real dispatch. Reserve
the worst-case tokens, one attempt and charge against the phase, tenant and
campaign limits; settle provider-reported usage after completion. Restarts and
concurrent processes share the same ledger and cannot reset caps. Unknown usage
retains the full reservation. Missing usage is null/unknown, never zero.
Report provider token counts separately from price-versioned calculated cost.
No account-wide cost guarantee is claimed for traffic outside this controlled
campaign. The account owner must isolate its project/key and review other usage.

Existing fake-only DB constraints and runtime must not simply be relaxed to
enable real calls. Review a separate migration, accounting and adapter design;
preserve tenant/actor FKs, identity/versioning, no DB transaction across I/O,
stale-result rejection, audit atomicity and non-repeatable unknown claims.

## Error and timeout policy

Zero automatic retries is deliberately stricter than the parent contract's
at-most-two-attempt ceiling. Disable retries in every client/SDK layer and prove
one transport dispatch with mocks. Enforce the end-to-end deadline across token
checks, reservation, dispatch and completion; a read timeout alone is insufficient.
After uncertain dispatch, timeout, disconnect or ambiguous server failure, retain
the claim and reservation as unknown; never send another generation automatically.

Map authentication rejection to the existing safe authentication category.
Distinguish throttling from exhausted credit/spend/quota via allowlisted error
codes; both stop this run rather than retrying or increasing limits. Never return
raw provider errors or refusal text. Documented 429 conditions are not all
transient throttling. [Error guidance](https://developers.openai.com/api/docs/guides/error-codes).

Refusal, truncation, invalid JSON/schema or extra output becomes a safe evaluation
failure with the case retained in denominators. Known usage must still be charged.
Reconciliation is reviewed operator work, not a retry button. Do not infer that
a client timeout means the provider did no work.

## Credentials and data

Propose a dedicated project-scoped API key injected by the named account owner
through a local process environment for an explicitly approved manual run, never
committed, pasted into chat, printed, included in exceptions or exposed in normal
PR CI. Name the owner and approve permissions/rotation before using any key.
Current owner and account/project access are unconfirmed; do not assume Eray's
AWS ownership also covers this provider account. Account creation is out of scope.

Send only reviewed synthetic subject/body and versioned instructions/schema.
Propose `store=false`; it is not a claim of zero retention. Official documentation
describes abuse-monitoring retention and endpoint-specific exceptions separately
from application state. Confirm the account's data controls before the run; no
customer ticket data is authorized by this proposal.
[Data controls](https://developers.openai.com/api/docs/guides/your-data).

## Quality and evidence gates proposed for approval

Retain the parent's proposed held-out thresholds: six-category macro-F1 >= 0.80,
abstention precision >= 0.80, abstention recall >= 0.75, zero schema failures and
zero provider errors. Undefined metrics fail acceptance rather than being omitted.
On this tiny 16-case set, include integer supports, confusion matrix, per-class
metrics, classified/abstained coverage, errors, refusals, latency samples/median/
p95/max and actual token usage plus calculated cost. No general accuracy or SLA
claim follows from passing these engineering gates.

Tune only on the 32 development cases. Record each development prompt version;
freeze model, prompt, schema, settings and corpus hashes before the held-out run.
Keep all 16 held-out cases, including shortcuts and failures, in the report. A
failure or budget stop is evidence, not permission to rerun. Any tuning after
held-out inspection requires a fresh jointly reviewed held-out set. Before that
run, confirm neither contributor used the held-out labels for prompt tuning.

## Approval checklist and next PR boundary

- [ ] Both contributors accept or revise the provider/snapshot and quality gates.
- [ ] Account owner accepts exact request/token/spend ceilings, currency exclusions,
      credential procedure and synthetic-only data controls.
- [ ] Exact official Python SDK version and transitive transport versions are
      selected, source-verified and approved; nothing is installed in this PR.
- [ ] Request fields, timeout/retry controls, usage fields and conservative token
      accounting are verified for those pinned versions; public reference review
      alone is not version-specific transport or token-bound evidence.
- [ ] Mock-only adapter/accounting implementation scope is approved after all
      above decisions are recorded; its PR must prove reservation concurrency,
      rollback, exhaustion, unknown usage and no-network default CI.
- [ ] After implementation review, a separate explicit run approval records exact
      commit, corpus/prompt/config hashes, tenant, phase, operator and remaining
      campaign allowance. No model call occurs before this final run gate.

Reading, reviewing or merging this proposal alone does not activate any cap or
authorize an API key check. #59 remains open until real-provider evidence is
jointly accepted, or the team explicitly approves a reduced fake-only milestone.
Kafka, live AWS, customer-data rollout and A2/A3 implementation remain separate.
