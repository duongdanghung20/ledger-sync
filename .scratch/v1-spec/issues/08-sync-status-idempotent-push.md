# Sync status and idempotent QBO push

Type: grilling
Status: resolved
Blocked by: 01, 06

## Question

Design how Journal Entries are pushed to a Book's QuickBooks company, tracked, and safely retried.

- Per-Journal-Entry Sync Status model (pending / posted / failed) with timestamps and the returned QBO id.
- Idempotency (ticket 01, `research/qbo-integration.md`): use the `requestid` query param (replay-safe, UUID, unique per realm) backed by a stable `DocNumber`/`PrivateNote` carrying the entry ID and query-before-retry on 5xx/network failure — `DocNumber` alone does not dedupe.
- Failure and retry flow; how errors surface to the user.
- Partial-batch failure handling (batch endpoint ≤30 payloads, 40 batch/min per realm).

Correctness: retries must never double-post.

## Answer

Approved Journal Entries are pushed to a Book's QuickBooks company **one per API call, synchronously**, with per-entry idempotency that guarantees a retry never double-posts. See [ADR-0005](../../../docs/adr/0005-idempotent-journal-entry-push.md).

**Transport (Q1).** One `POST /v3/company/{realmId}/journalentry` per Journal Entry, looped with pacing under the 10/s + 500/min per-realm limits. Not the `batch` endpoint: v1 volume (one Bookkeeper, hundreds of entries) fits a single request budget, and single calls give the cleanest idempotency (one `requestid`, no `bId` replay rule) plus free per-entry failure isolation. `ponytail:` ceiling — move to `batch` (≤30/call, ≤40/min) if a Book pushes thousands per session.

**Execution (Q2).** Synchronous, inline in the push request — no background worker/broker. Each entry's Sync Status is committed as it posts, so a mid-push stop leaves already-posted entries `posted` and the rest `pending`. Progress streamed to the UI (ticket 09). `ponytail:` ceiling — add a worker only if push routinely exceeds a request budget.

**Idempotency (Q3, correctness core).** Each Journal Entry gets a stable `requestid` (UUID) minted **at approval** (when the JE row is materialized, ticket 06), persisted, and **reused on every retry, never regenerated**. QBO replays the original response for a repeated `requestid` per realm, so retries are idempotent by construction; regenerating would be the only way to double-post, so we never do.

**Correlation fields (Q4).** `DocNumber` = the Journal Entry's id: stable, human-traceable in QBO, and the lookup key for query-before-retry. `DocNumber` has a ~21-char cap and does **not** itself dedupe, so if JE ids are UUIDs use a short monotonic per-Book sequence for `DocNumber` and stamp the full JE id + transaction description/payee into `PrivateNote`.

**Sync Status states (Q5).** Kept at exactly {`pending`, `posted`, `failed`} — no glossary change (CONTEXT.md unchanged). Concurrency (double-click / two Bookkeepers) is guarded by a **per-Book push lock** (one push at a time per Book), not a transient `posting` state. A crash mid-push leaves the entry `pending`; the next push recovers it via query-before-retry, never double-posting. Pre-push validation failures are `failed` + a reason code, not a separate state.

**Transitions.**
- `pending → posted`: JE created in QBO (2xx) → store returned `qbo_id` + `posted_at`.
- `pending → failed`: definite failure (see retry classification) → store error fields.
- `failed → posted` / `failed → failed`: manual re-push (reuses the same `requestid`).
- `posted`: terminal, immutable (ticket 06).

**Retry model (Q6).** No timer/auto-retry (sync, no worker). Retry = Bookkeeper re-pushes `failed` entries for the Book; each reuses its persisted `requestid`. Classify each push outcome:
- **`4xx` validation** → entry not created → `failed` + reason, safe to re-push after fix, no query needed.
- **`5xx` / network / timeout** (ambiguous) → **query QBO by `DocNumber` first**: found → `posted` (+ `qbo_id`); absent → `failed`, safe to re-push.
- **`429` throttle** → do not sleep 60s inside the request; **stop the push**, leave remaining entries `pending`, surface "rate limited, retry shortly". Already-posted stay `posted`.
- **Pre-push validation** (Q5): a JE whose snapshotted category/cash Account is now inactive (ticket 03), or whose source Bank Account is unmapped (ticket 06) → skip as `failed` + reason, **continue** posting the rest.

No in-request auto-retry in v1. `ponytail:` ceiling — add bounded backoff on transient 5xx if they prove common.

**Pre-push validation (from 03/06).** Before posting each entry, re-check its snapshot against the current Chart-of-Accounts mirror: both Account `qbo_id`s Active, and the source Bank Account has its `qbo_account_id`. A failing entry is flagged `failed` (`validation_inactive_account` / `validation_unmapped_bank`) and skipped; the rest of the push proceeds.

**Stored error data (Q7).** Four fields on the Journal Entry: `last_error` (QBO fault message text), `last_error_code` (normalized: a QBO code such as `2500`/`610`, or `http_5xx`/`network`/`throttled`/`validation_inactive_account`/`validation_unmapped_bank`), `last_attempt_at`, `attempt_count`. No attempt-history/audit table (YAGNI). Error **display** belongs to ticket 09; 08 defines the stored fields.

**Schema (feeds unified model).** The Journal Entry's sync fields (reserved by 06) are finalized: `sync_status` {pending,posted,failed}, `qbo_id`, `requestid`, `DocNumber`, `posted_at`, `last_error`, `last_error_code`, `last_attempt_at`, `attempt_count`. The per-Book push lock is application-level (a DB advisory lock or single-flight guard keyed by Book), not a schema field.

**Downstream.** Ticket 09 (review/approve UI) triggers approval (JE materialized + `requestid` minted) and the push action, shows Sync Status + `last_error`, and offers "retry failed".
