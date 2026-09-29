# Double-entry journal model

Type: grilling
Status: resolved
Blocked by: 03, 05

## Question

Design how a categorized Imported Transaction becomes a balanced Journal Entry (debits equal credits), scoped to the Book's QuickBooks company. Core money-correctness decision — call `grilling` and `domain-modeling`. Facts (ticket 01, `research/qbo-integration.md`): `JournalEntry`, minimum 2 lines, debits must equal credits (error 2300), positive amounts with sign via `PostingType` (Debit/Credit).

- The two sides: the source Bank Account's mapped QBO Account vs the category Account. Which is the counter-side, and how a Bank Account maps to a QBO Account.
- Cardinality: one Imported Transaction → one Journal Entry (two lines)? Any batching?
- Sign → `PostingType` (Debit/Credit) mapping, and the guarantee that every Journal Entry balances.
- Splits: one transaction across multiple category Accounts — in or out of v1?

## Answer

A categorized Imported Transaction becomes exactly one balanced two-line Journal Entry, scoped to the Book's QuickBooks company.

**Two sides (Q1, Q4).**
- Category side: the single target Account from categorization (ticket 04) — Active, excludes AR/AP.
- Cash side (the counter-side): the source Bank Account's mapped QBO Account. New required 1:1 link — each Bank Account → one QBO Account, chosen from the Book's Chart-of-Accounts mirror (ticket 03) at Bank Account setup. The picker suggests `AccountType=Bank` / `Classification=Asset`, but any Active Account is allowed. A Bank Account with no mapping has its transactions blocked from journal build and flagged, never silently dropped.

**Sign → PostingType, and the balance guarantee (Q4).** For signed canonical amount `A` (neg = outflow, ticket 05):
- `A < 0` (outflow): Debit category `|A|`, Credit cash `|A|`.
- `A > 0` (inflow): Debit cash `|A|`, Credit category `|A|`.

Both lines post `abs(A)` with opposite `PostingType`, so debits equal credits by construction and QBO error 2300 is structurally impossible. The rule is account-type-agnostic: bank→bank transfers, refunds into an expense, and credit-card paydowns all balance with no special case. `A == 0` is rejected (no zero-amount Journal Entry).

**Cardinality (Q3).** 1 Imported Transaction : 1 Journal Entry, always exactly 2 lines. No merging of multiple transactions into one entry. (The QBO batch endpoint in ticket 08 batches API calls, not entry contents.)

**Splits (Q2).** Out of v1. One transaction maps to exactly one category Account (ticket 04), so every entry is 2 lines. Recorded on the map's Out of scope.

**Money representation (Q5).** `Decimal` at 2 dp end to end (parse → store → push); never float. Single currency = the Book's QBO company home currency; multi-currency is out of v1 (omit line `CurrencyRef`, assume home). Ticket 05 parses amount as a bare number with no currency column, so v1 treats every transaction as home currency.

**Journal Entry identity & persistence (Q6).** A Journal Entry is fully derived from (Imported Transaction, resolved category Account `qbo_id`, Bank Account's QBO Account `qbo_id`, the rule above), so there is no independent line data and no JE-lines table. Model it as a thin `JournalEntry` entity, 1:1 FK to the Imported Transaction, storing:
- its own id — stable, used for `DocNumber` and audit;
- a snapshot — the two Account `qbo_id`s, `abs(amount)`, and which side is Debit vs Credit;
- a memo — the transaction `description`/`payee`, pushed to QBO line `Description` + `PrivateNote` for traceability;
- the sync fields owned by ticket 08 — `sync_status` (pending/posted/failed), `qbo_id` (returned JournalEntry id), `requestid`, `DocNumber`, `posted_at`, last error.

The snapshot makes a posted entry immutable to later rule re-runs or re-categorization. The two implied lines are reconstructed from the two snapshotted Accounts + amount + rule.

**Lifecycle (Q7).** Flow: imported → categorized → approved → posted.
- Created at approval — the approve action (ticket 09) materializes and snapshots the entry. Pre-approval, no Journal Entry exists.
- `pending` (approved, not posted): un-approve deletes the pending Journal Entry; the transaction falls back to categorized and may be re-categorized freely.
- `posted`: immutable. Editing/reversing posted entries stays out of scope — done in QBO directly.

**Downstream.**
- Ticket 08 (now unblocked): owns push, Sync Status transitions, `requestid`/`DocNumber` idempotency, retry, and partial-batch handling, attaching to the sync fields defined here.
- Ticket 09 (now unblocked): the approve action is the Journal Entry creation trigger, and shows the pending/posted states.
- New data-model requirement: Bank Account gains a required `qbo_account_id` (its mapped cash Account) — feeds the unified schema assembly.
