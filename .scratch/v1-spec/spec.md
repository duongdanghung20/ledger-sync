# Ledger-Sync v1 — buildable spec

Labels: ready-for-agent
Status: ready-for-agent

Synthesized from the v1-spec wayfinder map and its nine resolved decision tickets (01–09), ADR-0001…0005, and `CONTEXT.md`. This spec locks the v1 destination for `/to-tickets` → `/implement`. It records decisions, not code. Domain terms are used exactly as defined in `CONTEXT.md`.

---

## Problem Statement

A small business (an Organization) keeps its books in QuickBooks Online but receives its raw activity as CSV exports from one or more banks. Today a Bookkeeper turns those exports into QuickBooks entries by hand: opening each CSV, deciding which ledger Account each line belongs to, working out the debit and credit sides so the entry balances, and typing each one into QuickBooks. This is slow, error-prone, and easy to get wrong on the money path — a mistyped sign, a duplicated row from an overlapping export, or a double-posted entry after a failed save all corrupt the books.

The people affected:

- The **Bookkeeper**, who does the daily work of importing, categorizing, and posting, and who bears the cost of every duplicate or unbalanced entry.
- The **Admin**, who owns the Organization's setup: connecting QuickBooks, adding Users, and defining which real-world Bank Account maps to which QuickBooks ledger Account.

They need a self-hosted tool they can run on their own infrastructure (no third-party SaaS holding their financial data) that takes a bank CSV and reliably produces balanced, de-duplicated, correctly-signed double-entry Journal Entries in the right QuickBooks company — and never double-posts, even when the network fails mid-save.

## Solution

Ledger-Sync is a self-hosted web application (Next.js frontend, FastAPI backend, PostgreSQL, packaged with Docker Compose) that one Organization runs for itself. It serves one Organization whose financial data is organized into one or more Books; v1 auto-creates a single Book per Organization.

The workflow, from the user's perspective:

1. On first boot the operator sets an environment variable naming the first Admin; the app creates the Organization, its initial Book, and that Admin, and prints a single-use set-password link to the container logs.
2. The Admin logs in, connects the Book to a QuickBooks company over OAuth, and Ledger-Sync mirrors that company's Chart of Accounts read-only.
3. The Admin creates each Bank Account (checking, card) and maps it one-to-one to its cash-side QuickBooks Account.
4. A Bookkeeper uploads a bank CSV. Ledger-Sync recognizes the bank from a saved Column-Mapping Profile, parses the rows into canonical Imported Transactions, de-duplicates against what was already imported, and shows any rejected or possibly-duplicate rows rather than guessing.
5. Categorization Rules assign each Imported Transaction to one target Account automatically; the Bookkeeper manually categorizes or overrides the rest.
6. The Bookkeeper reviews the categorized transactions in whichever of three interchangeable views they prefer (dense Ledger table, one-at-a-time Focus queue, or Pipeline board), approves them — which materializes a balanced two-line Journal Entry for each — and pushes them to QuickBooks.
7. Each push is idempotent: a retry after a failure never double-posts. Every Journal Entry shows its Sync Status (pending, posted, or failed); failed entries surface the QuickBooks error and offer a safe manual retry.

The money path — dedup correctness, debit/credit balance, correct signs, and no-double-post idempotency — is held to a strict correctness bar and is never simplified away. Everything else follows the smallest-thing-that-works principle.

## User Stories

### Setup & bootstrap

1. As an operator, I want to bootstrap the first Admin from an environment variable on first boot, so that I can stand up a fresh instance without an open, unauthenticated setup screen.
2. As an operator, I want the first-boot set-password link printed to the container logs, so that no password is ever stored in a compose file or environment variable.
3. As an operator, I want the Organization name to come from an environment variable (or a sensible default), so that I can name the instance at deploy time and edit it later.
4. As an operator, I want the app, database, and reverse proxy to come up together under Docker Compose with documented environment variables, so that I can self-host the whole stack in one command.

### Authentication & accounts

5. As a User, I want to log in with an email and password, so that only authorized people reach the Organization's financial data.
6. As a User, I want my session to be an httpOnly cookie backed server-side, so that my login cannot be read or stolen by client-side scripts.
7. As a User, I want my session to expire after a fixed period, so that an abandoned login does not stay valid forever.
8. As an Admin, I want a disabled User's and a role-changed User's live sessions revoked immediately, so that removing access takes effect at once rather than at token expiry.
9. As an invited person, I want to set my password through a single-use, expiring link, so that I can join without the Organization needing email infrastructure.
10. As an Admin, I want to trigger a password reset that reissues the same set-password link, so that I can recover a User who forgot their password without a self-service email flow.

### Users & roles

11. As an Admin, I want to invite a new User with a chosen Role and receive a set-password link to deliver out-of-band, so that I can add people without email.
12. As an Admin, I want to change a User's Role, so that I can promote or restrict access as the team changes.
13. As an Admin, I want to disable a User, so that I can revoke access without deleting the record.
14. As a Bookkeeper, I want to do the full operational money path (import, categorize, refresh accounts, review, approve, push) without Admin rights, so that I can work without being blocked on an Admin.
15. As an Admin, I want QuickBooks Connection management, User management, and Bank Account creation/mapping restricted to Admins, so that money-path configuration is not changed by mistake.
16. As a User, I want every authorization decision enforced on the server per endpoint, so that a tampered frontend cannot grant access the server would deny.

### QuickBooks connection

17. As an Admin, I want to connect a Book to a QuickBooks company through an in-app OAuth flow, so that I can authorize the data connection myself.
18. As an Admin, I want to reconnect through the same self-serve flow when a connection expires or is revoked, so that I can restore syncing without operator help.
19. As an operator, I want the QuickBooks app credentials supplied via environment variables and never stored in the database or UI, so that one registered Intuit app can serve the instance safely.
20. As a User, I want each Book's OAuth refresh token encrypted at rest with a key held outside the database, so that a database dump alone cannot decrypt it.
21. As a User, I want access tokens refreshed on demand and the rotated refresh token persisted atomically, so that the Book is never locked out by a lost token.
22. As an Admin, I want a connection that fails to refresh marked disconnected with pushes blocked and a Reconnect prompt, so that I am told to act rather than silently failing to post.

### Chart of Accounts

23. As a Bookkeeper, I want the Book's Chart of Accounts mirrored read-only from QuickBooks, so that I categorize against the company's real accounts and Ledger-Sync never writes accounts back.
24. As a User, I want the Chart of Accounts fetched on connect and refreshable on demand, plus lazily refreshed before I review or push, so that a stale mirror never blocks or corrupts a post.
25. As a User, I want the mirror to include inactive accounts, so that historical references still resolve and deactivated accounts do not silently reappear.
26. As a User, I want accounts renamed, retyped, or deactivated in QuickBooks reconciled into the mirror on refresh (never hard-deleted locally), so that the mirror tracks the source of truth without losing references.
27. As a Bookkeeper, I want to pick any active Account as a categorization target, grouped by Classification, so that purchases, deposits, transfers, loan payments, and owner draws all categorize correctly.
28. As a Bookkeeper, I want Accounts-Receivable and Accounts-Payable account types excluded from categorization targets, so that I cannot build an entry QuickBooks will reject for a missing customer/vendor reference.
29. As a User, I want each Book to show when its Chart of Accounts was last synced, so that I can judge staleness at a glance.

### CSV import, column mapping, dedup

30. As a Bookkeeper, I want to import an arbitrary bank CSV, so that I am not limited to one bank's format.
31. As a Bookkeeper, I want a saved Column-Mapping Profile per bank that maps that bank's columns to the canonical fields, so that I configure a bank once and reuse it.
32. As a Bookkeeper, I want the right profile auto-suggested from the uploaded file's header signature, so that I usually just confirm rather than re-select.
33. As a Bookkeeper, I want both a single signed amount column (with an optional sign flip) and separate debit/credit columns supported, so that either bank convention imports correctly.
34. As a Bookkeeper, I want to choose or create the source Bank Account at import time (defaulted by the profile), so that every Imported Transaction carries its Bank Account.
35. As a Bookkeeper, I want re-imports of overlapping files de-duplicated so that no transaction is double-imported, so that my books are not inflated by re-exported rows.
36. As a Bookkeeper, I want two genuinely identical same-day transactions both kept, so that dedup never drops a real distinct transaction.
37. As a Bookkeeper, I want dedup to prefer the bank's stable unique id when present, so that re-imports are de-duplicated with certainty.
38. As a Bookkeeper, I want possible in-file duplicates surfaced as a count for me to review rather than silently decided, so that I stay in control of ambiguous cases.
39. As a Bookkeeper, I want malformed or partial rows collected into a rejected list with a reason while the valid rows still import, so that one bad row does not block a file and nothing is silently dropped.
40. As a Bookkeeper, I want dedup scoped per Bank Account, so that the same date/amount/description in two different Bank Accounts is treated as two distinct transactions.

### Categorization

41. As a Bookkeeper, I want to write Categorization Rules matching payee, description, amount, and date, so that recurring transactions categorize themselves.
42. As a Bookkeeper, I want text conditions (contains, equals, case-insensitive) and numeric/date conditions (gte, lte, between), so that I can express realistic matches.
43. As a Bookkeeper, I want conditions within a rule AND-ed and OR expressed as a second rule, so that the rule model stays flat and predictable.
44. As a Bookkeeper, I want rules evaluated first-match-wins by priority so that each transaction gets exactly one target Account, so that the journal has one clean counter-side.
45. As a Bookkeeper, I want amount conditions matched against the signed canonical amount (negative = outflow), so that "purchases of $100+" is expressed unambiguously.
46. As a Bookkeeper, I want conflicts resolved by silent priority tiebreak, so that I am not nagged about every overlapping rule.
47. As a Bookkeeper, I want to manually categorize any uncategorized transaction, so that rows no rule matched still get an Account.
48. As a Bookkeeper, I want a manual override to beat rules and survive re-running categorization, so that my deliberate choice is never clobbered.
49. As a Bookkeeper, I want each categorized transaction to record its source (rule, manual, or none) and which rule matched, so that I can trace why it landed where it did.
50. As a Bookkeeper, I want a rule whose target Account has gone inactive kept, flagged invalid, and skipped at match time, so that the transaction falls through to manual rather than categorizing onto an account QuickBooks will reject.

### Double-entry journal

51. As a Bookkeeper, I want each categorized transaction to become exactly one balanced two-line Journal Entry, so that every posting is correct double-entry by construction.
52. As an Admin, I want each Bank Account mapped one-to-one to a cash-side QuickBooks Account at setup, so that every entry has a defined counter-side.
53. As a Bookkeeper, I want a transaction whose Bank Account is unmapped blocked from journal build and flagged, so that it is never silently dropped or posted wrong.
54. As a Bookkeeper, I want outflows to debit the category Account and credit cash, and inflows to debit cash and credit the category Account, both for the absolute amount, so that debits always equal credits regardless of account type.
55. As a Bookkeeper, I want transfers, refunds, and card paydowns handled by the same sign rule with no special case, so that unusual transactions still balance.
56. As a Bookkeeper, I want a zero-amount transaction rejected rather than turned into an entry, so that no empty Journal Entry is created.
57. As a Bookkeeper, I want amounts handled as two-decimal-place decimals end to end (never float), so that money is never corrupted by rounding.
58. As a Bookkeeper, I want a posted Journal Entry to snapshot its accounts and amount so it is immutable to later rule re-runs or re-categorization, so that history does not shift under me.
59. As a Bookkeeper, I want the transaction's description/payee carried into the entry's memo, so that the posting is traceable in QuickBooks.

### Review, approval, push

60. As a Bookkeeper, I want a default dense Ledger table with state-filter tabs, inline Account pickers, and checkbox bulk approve/push, so that I can process a batch quickly.
61. As a Bookkeeper, I want a Focus queue that shows the full debit/credit preview and balance proof one transaction at a time with keyboard approval, so that I can review the money path closely when I want to.
62. As a Bookkeeper, I want a Pipeline board with a lane per state and per-card or whole-lane advance, so that I can work the queue as a state machine.
63. As a Bookkeeper, I want to switch freely between the three views over the same data and operations, so that layout is a preference, not a different feature.
64. As a User, I want my chosen view remembered across sessions, so that I return to the way I like to work.
65. As a Bookkeeper, I want approval barred for an uncategorized transaction, so that I cannot post something with no Account.
66. As a Bookkeeper, I want approve to materialize the balanced Journal Entry and un-approve to discard a still-pending entry back to categorized, so that I can revise before posting but not after.
67. As a Bookkeeper, I want to reach the debit/credit preview from any view, so that I can check the entry before approving anywhere.
68. As a Bookkeeper, I want a failed entry to surface its error and offer a manual retry in every view, so that I never lose track of what did not post.
69. As a Bookkeeper, I want the four operations (assign/override Account, approve, push, retry) to behave identically regardless of view, so that switching views never changes what an action does.

### Sync status & idempotent push

70. As a Bookkeeper, I want each approved Journal Entry pushed to QuickBooks one at a time, synchronously, so that I get per-entry success/failure and simple, correct idempotency.
71. As a Bookkeeper, I want a retry after any failure to never double-post, so that the books are never inflated by a duplicate.
72. As a Bookkeeper, I want a crash mid-push recovered on the next push without duplicating anything, so that an interruption is safe.
73. As a Bookkeeper, I want validation failures (inactive target Account, unmapped Bank Account) to skip just that entry and continue posting the rest, so that one bad entry does not block the batch.
74. As a Bookkeeper, I want a rate-limit response to stop the push and leave the rest pending with a "retry shortly" message, so that already-posted entries stay posted and nothing is lost.
75. As a Bookkeeper, I want each entry's Sync Status, last error, last attempt time, and attempt count visible, so that I understand the state of every posting.
76. As a Bookkeeper, I want concurrent pushes on the same Book serialized, so that two clicks or two people cannot race the same entries.

## Implementation Decisions

Binding architecture decisions are recorded in ADR-0001 (single-tenant, Organization-scoped), ADR-0002 (QuickBooks Online is the source of truth for accounts), ADR-0003 (Book as the accounting-entity boundary), ADR-0004 (local passwords, no external IdP), and ADR-0005 (idempotent one-per-call synchronous push). This spec assembles the settled per-ticket decisions into a buildable whole; it does not reopen them.

### Stack & packaging

- **Frontend:** Next.js + React. **Backend:** FastAPI (Python). **Database:** PostgreSQL. **Packaging:** Docker Compose (app services + Postgres + a reverse proxy fronting both at a single origin).
- Frontend and backend sit behind one reverse proxy at `APP_BASE_URL` and are treated as **same-origin**; this is the assumption the session-cookie design depends on.
- **Fetch current library/framework/API documentation via Context7 before writing any code** against Next.js, React, FastAPI, psycopg, httpx, the QuickBooks Online API, pytest, testcontainers, Vitest, Playwright, Tailwind, shadcn/ui, TanStack Query, or Zod — never from memory. This stack has fast-moving surfaces.
- All code follows the smallest-thing-that-works principle; frontend work additionally uses the frontend-design skill.

### Modules

The backend is organized into these responsibility areas (module names, not file paths):

- **Auth & session** — login, logout, set-password redemption, session issuance/validation middleware, session revocation on User disable/role change.
- **Users & Invitations** — Admin-only User CRUD, role change, disable, Invitation issuance (returns the set-password link), Admin-triggered reset.
- **QuickBooks Connection** — per-Book OAuth authorization-code flow, callback handling, token storage/refresh, connection status.
- **QuickBooks client (the single external seam)** — the one adapter wrapping every outbound QuickBooks Online HTTP call: token refresh, Account query, `journalentry` POST with `requestid`, and query-by-`DocNumber`. All other modules depend on this port, never on `httpx` directly.
- **Chart of Accounts mirror** — fetch, cache, reconcile (upsert by `qbo_id`, absent → `Active=false`), refresh triggers, categorization-target selection.
- **CSV import** — parsing (Python stdlib `csv`), Column-Mapping Profile application, per-Bank-Account dedup, malformed-row rejection.
- **Categorization engine** — the pure rule matcher (`matchCondition`, `categorizeOne`, `categorize`) lifted from the ticket-04 prototype, plus rule CRUD and manual override.
- **Journal** — materialize a balanced two-line Journal Entry from a categorized transaction at approval; enforce the sign rule and the balance guarantee.
- **Push & sync** — the idempotent one-per-call synchronous push, Sync Status transitions, pre-push validation, retry classification, per-Book push lock.
- **Review** — read models for the three views and the four operations; presentation is frontend-only over this one operation set.

### Key interfaces & contracts

**QuickBooks client port** (the seam faked in tests) exposes at least: `refresh_token(connection)`, `query_accounts(connection)` returning the full mirror set (`Active IN (true,false)`), `post_journal_entry(connection, entry, requestid)`, and `find_journal_entry_by_doc_number(connection, doc_number)`. It surfaces HTTP status classes (2xx / 4xx / 5xx / network / 429) to the caller for retry classification.

**Column-Mapping Profile** (shape validated in the ticket-05 prototype against three real sample layouts; encodes the sign-convention decision):

```jsonc
{
  "name": "Chase Checking",
  "bank_account": "<default Bank Account id>",   // overridable per import
  "delimiter": ",",
  "decimal": "dot",                               // "dot" (1,234.56) or "comma" (1.234,56)
  "header_row": 0,
  "skip_trailing": 0,
  "date": { "column": "Posting Date", "format": "MM/DD/YYYY" },
  "description": { "column": "Description" },
  "payee": { "column": "Counterparty" },          // optional
  "external_id": { "column": "Transaction ID" },  // optional
  "amount": { "mode": "signed", "column": "Amount", "flip": false }
  // OR: "amount": { "mode": "debit_credit", "debit_column": "Debit", "credit_column": "Credit" }
}
```

**Dedup contract** (per Bank Account): if `external_id` is present, dedup on `(bank_account_id, external_id)` alone. Otherwise the key is `hash(date, signed amount, description, payee)`, reconciled as a **multiset** — an incoming row is a duplicate only if an already-imported row with the same key is still unmatched; extras beyond the existing multiplicity are new. This guarantees: never double-imports, never drops same-day genuine identicals.

**Sign rule** (from the ticket-06 grilling; the balance guarantee, verified in the ticket-09 prototype). For signed canonical amount `A` (negative = outflow of the Bank Account):

```
A < 0 (outflow):  Debit category |A|,  Credit cash     |A|
A > 0 (inflow):   Debit cash     |A|,  Credit category |A|
A = 0:            rejected (no zero-amount Journal Entry)
```

Both lines post `abs(A)` with opposite `PostingType`, so debits equal credits by construction and QuickBooks error 2300 is structurally impossible. The rule is account-type-agnostic.

**Journal Entry** is a thin entity, one-to-one with the Imported Transaction, materialized at approval, storing a snapshot plus the sync fields (type shape, from ticket 06 + ticket 08):

```
JournalEntry {
  id                         // stable; basis for DocNumber and audit
  imported_transaction_id    // 1:1 FK
  category_account_qbo_id    // snapshot
  cash_account_qbo_id        // snapshot (the Bank Account's mapped Account)
  amount                     // Decimal(2dp), abs
  debit_side                 // {category | cash} — which side is the debit
  memo                       // description/payee → QBO Description + PrivateNote
  sync_status                // {pending, posted, failed}
  qbo_id                     // returned JournalEntry id, once posted
  requestid                  // UUID minted at approval; reused on every retry, NEVER regenerated
  doc_number                 // short monotonic per-Book sequence; full id also in PrivateNote
  posted_at
  last_error                 // QBO fault message text
  last_error_code            // e.g. 2500 / 610 / http_5xx / network / throttled /
                             //      validation_inactive_account / validation_unmapped_bank
  last_attempt_at
  attempt_count
}
```

There is **no Journal Entry lines table**; the two lines are reconstructed from the snapshot.

### Unified data model

The map declared the unified schema "pure assembly of the settled models." The entities and their decided fields:

- **Organization** — id, name.
- **User** — id, email, Argon2id password hash, role `{Admin, Bookkeeper}` (Organization-wide), disabled flag, must-set-password flag, nullable `preferred_review_view ∈ {table, queue, board}` (UI-only, outside RBAC; `ponytail:` may be dropped in favor of a `localStorage` key if a migration field is not worth it).
- **Session** — opaque id, User, created_at, expires_at (absolute 14-day expiry).
- **Invitation** — id, target identifier (email), role, token, expiry, consumed flag.
- **Book** — id, Organization, name, `last_synced_at` (Chart of Accounts). Exactly one per Organization in v1, auto-created at bootstrap; the schema carries Book from the start so a second entity needs no migration.
- **QuickBooksConnection** — id, Book (1:1), `realm_id`, access token + expiry, encrypted refresh token, status `{pending, connected, disconnected}`, connected_at.
- **Account** (Chart-of-Accounts mirror) — id, Book, `qbo_id`, name, `account_type`, `account_sub_type`, `classification`, `acct_num`, `active`, `sync_token`, `parent_qbo_id`.
- **BankAccount** — id, Book, name, **required `qbo_account_id`** (its 1:1 mapped cash-side Account).
- **ColumnMappingProfile** — id, Book, name, default Bank Account, config JSON (shape above).
- **ImportedTransaction** — id, Book, Bank Account, Column-Mapping Profile, `date`, signed `amount` (Decimal 2dp), `description`, optional `payee`, optional `external_id`, `imported_at`, retained raw source row; assigned Account, `category_source ∈ {rule, manual, none}`, matched rule id, dedup key.
- **CategorizationRule** — id, Book, priority, target Account, conditions JSON (`[{field, operator, value, value2?}]`), invalid-target flag.
- **JournalEntry** — as the type shape above.

**Transaction state is derived, not a second stored column** (`ponytail:` avoid a status field that can drift from the source of truth): *imported/uncategorized* = no Account assigned; *categorized* = Account assigned; *approved* = a Journal Entry exists (Sync Status `pending`); *posted* = Journal Entry Sync Status `posted`; *failed* is the Journal Entry Sync Status, surfaced in every view.

### Environment variables

`QBO_CLIENT_ID`, `QBO_CLIENT_SECRET`, `TOKEN_ENC_KEY` (32-byte, authenticated symmetric encryption via `cryptography` Fernet/AES-GCM), `APP_BASE_URL` (also fixes the OAuth redirect URI `{APP_BASE_URL}/api/qbo/callback`), `BOOTSTRAP_ADMIN_EMAIL`, `ORG_NAME` (optional), `SESSION_SECRET`. App credentials and the encryption key live only in the environment, never in the database.

### Cross-cutting rules

- **Authorization is server-side, per route** — a FastAPI dependency asserts the required role; the frontend is never trusted. Admin-exclusive: QuickBooks Connection, User management, Bank Account creation + `qbo_account_id` mapping.
- **The money path is never simplified away**: dedup correctness, debit/credit balance, correct signs, and no-double-post idempotency are the correctness bar.
- **QuickBooks is the source of truth for accounts** (ADR-0002); Ledger-Sync never writes `Account` back in v1.
- **Refresh tokens encrypted at rest; tokens never logged.**

## Testing Decisions

**What makes a good test here:** it asserts on externally observable behavior (an HTTP response, a row's resulting state, the debit/credit lines produced, whether a second push created a duplicate in the fake QuickBooks) — never on internal call sequences or private structure. The money-path guarantees are the tests that matter most and must hold regardless of refactoring beneath them.

**Three seams, exercised in priority order** (per the developer's ruling):

1. **API boundary + faked QuickBooks client port (primary — the bulk of coverage).** Drive the FastAPI app in-process (httpx `ASGITransport`) against a real Postgres. The **only** faked external boundary is the QuickBooks client port; everything else runs for real. This seam covers auth and RBAC (each Admin-only route rejects a Bookkeeper), CSV import and dedup, categorization and override, approval → Journal Entry materialization, push, Sync Status transitions, pre-push validation, and retry classification. The fake QuickBooks implements `requestid` replay (same `requestid` → same response, no duplicate), query-by-`DocNumber`, the Account query, and token refresh, and can be driven to return 4xx / 5xx / network / 429 to exercise every retry branch.
2. **Service-layer seams (the pure engines).** Table-driven unit tests directly against the lifted pure logic: the categorization matcher (`matchCondition`/`categorizeOne`/`categorize`), CSV parse + multiset dedup, and the sign → PostingType rule. These are cheaper than HTTP round-trips for the dense combinatorial cases and are lifted straight from the validated prototypes.
3. **End-to-end via UI (thin top layer).** Playwright over a running Next.js + FastAPI stack with a fake QuickBooks, covering the three review views, view switching over shared data, and the happy-path import → categorize → approve → push flow.

**Database in tests:** ephemeral Postgres via **testcontainers** — a disposable instance per run with migrations applied and state reset per test (transaction rollback or truncate). This is required, not optional: the per-Book push lock uses a real Postgres advisory lock and the money tables rely on real constraints, so SQLite would leave exactly those guarantees untested.

**Money-path correctness tests (the bar the spec is held to):**

- **Dedup:** never drops distinct transactions and never double-imports — both the `external_id` path and the content-multiset path, including same-day genuine identicals and re-imported overlapping windows. (The ticket-05 headless check already passes 7/7 and seeds this corpus.)
- **Balance & sign:** every materialized Journal Entry has debits equal to credits; outflow debits category / credits cash and inflow debits cash / credits category; `A = 0` is rejected.
- **Idempotency:** a retry never double-posts — same-`requestid` replay returns the original entry; a crash mid-push is recovered via query-by-`DocNumber` and never duplicates; 429 leaves remaining entries `pending`; ambiguous 5xx/network queries by `DocNumber` then resolves to `posted` or `failed`; a 4xx is `failed` and safe to re-push after a fix.
- **Pre-push validation:** an entry with an inactive target Account or unmapped Bank Account is skipped as `failed` with the right code while the rest of the push continues.

**Prior art:** none — this is a greenfield repository, so these tests establish the patterns. The three prototypes (categorization, CSV import, review UI) are the primary source for the lifted logic and are kept in place (the repo is not under git). **Frameworks:** pytest + httpx for the API and service seams, Vitest + Playwright for the frontend; confirm current usage via Context7 before wiring.

## Out of Scope

Ruled beyond the v1 destination:

- **CI/CD pipeline** (e.g. GitHub Actions) — an infrastructure chore, not a spec decision.
- **In-app financial reporting** (P&L, balance sheet) — QuickBooks Online owns reporting.
- **Editing or reversing already-posted entries; any post-export lifecycle** — done in QuickBooks directly; posted Journal Entries are immutable.
- **Cross-customer multi-tenancy / SaaS** — ADR-0001.
- **ML / automatic categorization** — rules-based only.
- **Reading back existing QuickBooks transactions / reconciliation.**
- **Split transactions** (one transaction across multiple category Accounts) — one Account per transaction, every entry two lines.
- **Multi-currency Journal Entries** — each Book's QuickBooks company home currency only; no line `CurrencyRef`.
- **Multi-Book creation, a Book-switcher UI, and per-Book role scoping** — deferred; the schema carries Book so a second entity needs no migration.
- **Email / SMTP** — Invitations and resets use the out-of-band set-password link; adding SMTP later is non-breaking.
- **"Make this a rule" from a manual override** — deferred.
- **Background worker / batch push endpoint** — v1 pushes one per call synchronously; move to the batch endpoint and/or a worker only if volume demands it.
- **External identity provider (OIDC/SAML), password rotation, and breach-list (HIBP) checks** — ADR-0004.

## Further Notes

- **Deliberate ceilings (`ponytail:` upgrade paths), all accepted for v1:**
  - Dedup without a bank unique id is irreducibly ambiguous across non-aligned re-import windows; optionally add a running-balance column as an extra key component when a bank provides one. Never silently guess — surface the ambiguity.
  - One-per-call synchronous push has a single-request budget ceiling; a session pushing thousands of entries would move to the batch endpoint and/or a background worker.
  - `preferred_review_view` as a `User` column costs one migration field; `localStorage` is the zero-migration fallback if that is not worth it.
- **QuickBooks facts** the build depends on (full cited findings in `research/qbo-integration.md`): `JournalEntry` is the object for arbitrary debit/credit lines (min 2 lines, debits = credits, positive amounts with sign via `PostingType`); native idempotency via `requestid`; `Account` soft-deleted via `Active` (query `Active IN (true,false)`); 500 req/min + 10 req/s per realm.
- **Home currency only** across the whole pipeline; amounts are `Decimal` at two decimal places end to end, never float.
- The three prototypes and the QuickBooks research file are the authoritative source for lifted logic and API facts; prefer them over re-deriving.
