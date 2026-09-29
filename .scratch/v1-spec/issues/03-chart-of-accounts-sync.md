# Chart of Accounts sync

Type: grilling
Status: resolved
Blocked by: 01

## Question

Design how each Book's Chart of Accounts is fetched from its QuickBooks company, cached, and refreshed. QBO is the source of truth, per Book (ADR-0002).

- When is the Chart of Accounts fetched — on connect, on demand, on a schedule?
- Local cache shape and how staleness is handled. Accounts are soft-deleted via the `Active` flag — query `Active IN (true,false)` for a full mirror; cache `Id`/`SyncToken`/`Active` (ticket 01).
- Handling accounts added, renamed, or deactivated in QBO after the cache was populated.
- Which account types (`AccountType`/`Classification`) are selectable as categorization targets (feeds ticket 04).

## Answer

QuickBooks Online is the source of truth per Book (ADR-0002); Ledger-Sync holds a read-only mirror of each Book's Chart of Accounts. v1 never writes `Account` back to QBO.

**Q1 — Refresh trigger + staleness.** Full re-query of the Chart of Accounts on connect; a manual "Refresh Accounts" action for the Admin; plus a lazy auto-refresh when a Book opens the review screen or initiates a push, so a stale mirror never blocks a post. No background scheduler in v1 (the Chart of Accounts is small and changes rarely — a cron is infra the money path does not need). Staleness UX is a per-Book `last_synced_at` display, nothing more.

**Q2 — Mirror scope, cache fields, reconcile.** Mirror the full set including inactive accounts — query `WHERE Active IN (true,false)` (ticket 01) — so historical references still resolve and deactivated accounts do not silently reappear. Cached fields per account: `qbo_id`, `Name`, `AccountType`, `AccountSubType`, `Classification`, `AcctNum`, `Active`, `SyncToken`, `parent_qbo_id`. Reconcile on every refresh: upsert by `qbo_id` (stable, never reused — the local join key); an account present locally but absent from the QBO result is marked `Active=false` (soft), **never hard-deleted**; renames and type changes overwrite fields on upsert. `SyncToken` is cached but only needed for account writes, which v1 does not do — kept as informational, cheap. Skipped: storing the full raw JSON blob — add only if a later ticket needs a field not mirrored here.

**Q3 — Selectable categorization targets (feeds ticket 04).** All `Active` accounts are selectable as the counter-side Account, with no Classification restriction, grouped by Classification in the picker. A bank transaction's counter-side legitimately hits any of the five Classifications (Expense, Revenue, Asset, Liability, Equity — purchase, deposit, transfer, loan payment, owner draw), so restricting to Expense+Income would break real cases. Two carve-outs: (1) `Active=false` accounts are excluded from *new* selections but stay resolvable for existing references (Q4/Q5); (2) Accounts-Receivable and Accounts-Payable account types are **excluded from v1 selectable targets** — a `JournalEntry` line against AR/AP requires an `Entity` (customer/vendor) reference (ticket 01) that v1 does not collect. No filtering beyond that; QBO validates the post.

**Q4 — Categorization Rule pointing at a now-inactive/gone Account.** Keep the rule (never auto-delete user config); flag it "invalid target" in the rules UI; at categorization time treat it as no-match, so the affected Imported Transaction falls through to unmatched/manual. Not kept firing — that would categorize onto an account QBO rejects on push.

**Q5 — Un-pushed Imported Transaction whose target Account went inactive before push.** Block just that transaction from the approve/push batch and flag "target inactive — recategorize"; the rest of the batch posts. A `JournalEntry` line against an inactive Account is a guaranteed QBO rejection, so the local guard turns a sync failure into a fixable review item. The enforcement point at push time lives in ticket 08; this ticket fixes the policy. Merge / hard-gone collapses to the same case — reconcile (Q2) already marked the absent account `Active=false` — so no separate rule.

**Surfaced for the unified-schema fog:** a Book-scoped Account-mirror table with the Q2 field set plus a per-Book `last_synced_at`. Feeds the same fog line as import (05), rules (04), and journal (06); no new decision ticket.
