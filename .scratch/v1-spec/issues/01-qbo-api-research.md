# QBO API research

Type: research
Status: resolved

## Question

Establish the QuickBooks Online API facts that v1 depends on. Investigate against primary sources (Intuit developer docs, Context7) and capture cited findings at `.scratch/v1-spec/research/qbo-integration.md`.

Answer these:

- Which API object represents a posted double-entry line set — `JournalEntry` vs `Purchase`/`Deposit`/`Bill`? Confirm `JournalEntry` supports arbitrary debit/credit lines posted against Chart-of-Accounts accounts, and note any constraints (minimum lines, balancing, required fields).
- OAuth2 flow for a self-hosted app: app registration on the Intuit developer portal, client id/secret, redirect URI handling, required scopes (Accounting), access + refresh token lifetimes, and sandbox vs production differences.
- Reading the Chart of Accounts (the `Account` entity): fields, account types, active/inactive handling.
- Idempotency: does the API support a request idempotency key or another client-side mechanism to prevent double-posting on retry? If not, what field or identifier lets us detect an already-posted Journal Entry?
- Rate limits and any batch endpoints for reads/writes.

AFK: resolved by a research subagent.

## Answer

Full cited findings: [`research/qbo-integration.md`](../research/qbo-integration.md).

1. **Journal object**: `JournalEntry` is the object for arbitrary double-entry lines against Chart-of-Accounts accounts. Each line: `Amount`, `DetailType="JournalEntryLineDetail"`, `JournalEntryLineDetail.{PostingType (Debit|Credit), AccountRef.value}`. Minimum 2 lines; debits must equal credits (else error 2300); amounts are positive with sign carried by `PostingType` (negatives rejected, 2290); not gated by cash/accrual. `Purchase`/`Bill`/`Deposit` are specialized (fixed counter-accounts, vendor/AP semantics) — wrong for a generic push.
2. **OAuth2**: auth-code flow, scope `com.intuit.quickbooks.accounting`, access token ~1h, refresh token ~101 days (rotates). Registration on the Intuit developer portal; sandbox vs production companies.
3. **Account entity**: `Id`, `Name`, `AccountType`, `AccountSubType`, `Classification`, `AcctNum`, `CurrencyRef`, `Active`, parent hierarchy. Accounts are soft-deleted (`Active` flag) — query `WHERE Active IN (true,false)` for a full mirror; cache `Id`/`SyncToken`/`Active` on connect.
4. **Idempotency**: native — the `requestid` query param on writes guarantees replay (same `requestid` → same response, no duplicate); unique per realm, max 50 chars (36 batch, 10 per batch-id combined), use a UUID. Back with a stable client `DocNumber`/`PrivateNote` carrying the entry ID and query-before-retry on 5xx/network failure (`DocNumber` alone does NOT dedupe).
5. **Rate limits**: 500 req/min and 10 req/sec per realmID (800/min combined app-level); batch endpoint bundles create/update/delete/query, ≤30 payloads, 40 batch/min per realm+app; 429 → wait 60s; queries cap at 1000 rows (paginate).

**Multi-company**: QBO paths are company-scoped as `/v3/company/{realmID}/...`; one registered app authorizes per company, one token set per `realmId`, one instance holds many concurrently — no one-app-one-company limit. Confirms per-Book QuickBooksConnection is viable.

**Caveat**: default `Active` filter behavior is ambiguous in the docs — always query `Active IN (true,false)`, don't rely on the default. JS-rendered reference pages resisted automated fetch; facts anchored to source URLs, flagged for a human read where noted.
