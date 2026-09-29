# QuickBooks Online API — Integration Research (Ledger-Sync v1)

**Wayfinder ticket:** 01
**Date:** 2026-09-29
**Method:** Primary sources only — Intuit developer docs (developer.intuit.com), fetched via Context7 (`/websites/developer_intuit_app_developer_qbo`, sourced from developer.intuit.com) and targeted web search of developer.intuit.com / help.developer.intuit.com. Each claim is cited to the Intuit page that owns it.

**Scope reminder:** Ledger-Sync is self-hosted, single-tenant (one Organization = one QBO company/realm), multi-user. v1 reads the Chart of Accounts **down** and pushes **balanced Journal Entries up**, with per-entry sync status and idempotent retry. QBO is the source of truth for the Chart of Accounts.

---

## Q1 — Which object represents a posted double-entry line set? (`JournalEntry` vs `Purchase`/`Deposit`/`Bill`)

**Answer: `JournalEntry`.** It is the QBO object that posts an arbitrary set of debit/credit lines directly against Chart-of-Accounts accounts. It is the correct target for Ledger-Sync's "push a balanced double-entry journal" flow.

### Why not Purchase / Deposit / Bill
Those are *specialized* transaction objects, each with a fixed posting shape and extra semantics — they are not general-purpose debit/credit posters:

- **`Bill`** — a vendor bill on **accrual/Accounts Payable**. It carries `VendorRef` (required) and posts the credit side to an `APAccountRef` (Accounts Payable); lines are `AccountBasedExpenseLineDetail`, not free debit/credit. Use it to model "money owed to a vendor," not an arbitrary journal.
- **`Purchase`** — a cash/credit-card expense (an "expense object"): a purchase made from a vendor, paid from a bank/credit-card account. Fixed posting shape (payment account + expense lines).
- **`Deposit`** — money deposited into an account.

Each of those bakes in a counter-account and a business meaning. `JournalEntry` does not — you specify every line's account and posting side yourself, which is exactly what a rules-driven ledger push needs. (Object catalog / CRUD entities incl. `JournalEntry`, `Purchase`, `Deposit`, `Bill`: https://developer.intuit.com/app/developer/qbo/docs/develop/sdks-and-samples-collections/java/samples-gallery ; Bill's `VendorRef` + `APAccountRef` accrual shape: https://developer.intuit.com/app/developer/qbo/docs/workflows/manage-projects/use-cases ; expense-object definition: https://developer.intuit.com/app/developer/qbo/docs/workflows/manage-linked-transactions)

### Does `JournalEntry` support arbitrary debit/credit lines against CoA accounts? — Yes
Each line is a `JournalEntryLineDetail` line carrying:
- `Line.Amount` — the monetary value (positive; the sign is expressed by `PostingType`, not a negative amount — negative amounts are rejected, error 2290 `NegativeAmount`).
- `Line.DetailType` = `"JournalEntryLineDetail"`.
- `Line.JournalEntryLineDetail.PostingType` = `"Debit"` or `"Credit"`.
- `Line.JournalEntryLineDetail.AccountRef` = `{ "value": "<Account.Id from the Chart of Accounts>" }` — this is where a CoA account ID is bound to the line.

(PostingType Debit/Credit + AccountRef on JournalEntry lines: https://developer.intuit.com/app/developer/qbo/docs/workflows/manage-linked-transactions ; line shape `Amount`/`DetailType`/detail nesting is the standard REST line model: https://developer.intuit.com/app/developer/qbo/docs/learn/rest-api-features ; JournalEntry reference: https://developer.intuit.com/app/developer/qbo/docs/api/accounting/all-entities/journalentry)

### Constraints

- **Minimum lines:** at least **two** `Line` entries — at minimum one `PostingType=Debit` and one `PostingType=Credit`. A single-line entry cannot balance. (https://developer.intuit.com/app/developer/qbo/docs/api/accounting/all-entities/journalentry)
- **Balancing requirement (hard):** total debits must equal total credits, or the create/update is rejected with **error 2300 `Amount on debits not equal to credits` / "Please balance your debits and credits."** Ledger-Sync must balance the entry client-side before posting. (Error code table: https://developer.intuit.com/app/developer/qbo/docs/develop/troubleshooting/error-codes ; forum confirmation: https://help.developer.intuit.com/s/question/0D54R00008hn7OiSAI/)
- **Required fields:** `Line` (the array) is required. Each line requires `DetailType="JournalEntryLineDetail"`, and within `JournalEntryLineDetail`: `PostingType`, `AccountRef`, and `Line.Amount`. `AccountRef.value` must be a valid, **active** account ID (an inactive account ref triggers error 2500 / 610 — see Q3). Referenced accounts must exist in the CoA. (https://developer.intuit.com/app/developer/qbo/docs/api/accounting/all-entities/journalentry ; inactive-ref errors: https://developer.intuit.com/app/developer/qbo/docs/develop/troubleshooting/handling-common-errors)
- **Line-item cap:** the platform's 10,000-line-per-transaction cap is documented as applying to "each transaction **except** Journal Entry" — i.e. JE is explicitly carved out of that particular limit. Practically, keep entries modest. (https://developer.intuit.com/app/developer/qbo/docs/learn/limits-and-throttles)
- **Cash vs accrual:** creating a `JournalEntry` is **not** gated by the company's accounting method — a JE posts straight to the general ledger and appears in reports run on either basis (accounting method is a *report* parameter, e.g. `accounting_method = "Accrual"|"Cash"`, not a posting-time restriction on JEs). There is an optional `Adjustment` boolean to flag an adjusting entry. So both cash- and accrual-configured companies accept JE pushes. (Report accounting_method param: https://developer.intuit.com/app/developer/qbo/docs/develop/sdks-and-samples-collections/net/reports ; JournalEntry reference incl. `Adjustment`: https://developer.intuit.com/app/developer/qbo/docs/api/accounting/all-entities/journalentry) — *see caveat below.*

**Endpoint:** `POST /v3/company/{realmId}/journalentry` (minorversion query param recommended).

---

## Q2 — OAuth2 flow for a self-hosted app

QBO uses **OAuth 2.0 Authorization Code Grant**. (OpenID Connect is available additionally but not required for API access.)

### App registration (Intuit developer portal)
1. Create an app at developer.intuit.com. Each app has **two separate key sets**: **Development/Sandbox** keys and **Production** keys, each with its own `client_id` (Client ID) and `client_secret` (Client Secret), found under **Keys and credentials** in the left sidebar.
2. Configure the **Redirect URI** under the app's **Settings**. It must **exactly match** the `redirect_uri` sent in the auth request (the tutorial uses `http://localhost:8080/oauth2redirect`; for a self-hosted server use its real callback URL). Multiple redirect URIs can be registered.
3. Configure `client_id`, `client_secret`, and `redirect_uri` in the app's config. (https://developer.intuit.com/app/developer/qbo/docs/develop/sdks-and-samples-collections/java ; https://developer.intuit.com/app/developer/qbo/docs/develop/authentication-and-authorization/faq)

### Authorization request → code
Redirect the user (via a "Connect to QuickBooks" button) to:
```
https://appcenter.intuit.com/connect/oauth2?
    client_id=<Client ID>&
    response_type=code&
    redirect_uri=<registered redirect URI>&
    scope=com.intuit.quickbooks.accounting&
    state=<CSRF token>
```
Intuit authenticates the user and redirects back to `redirect_uri` with `code`, `state`, and **`realmId`** (the QBO company ID — required on every subsequent API call; the user picks *which* company to connect during this flow, so one authorization yields exactly one `realmId` + token set — see the Multi-company section below). (https://developer.intuit.com/app/developer/qbo/docs/develop/authentication-and-authorization/faq)

### Required scope (Accounting)
- `com.intuit.quickbooks.accounting` — grants access to the Accounting API (Account, JournalEntry, etc.). This is the only scope Ledger-Sync v1 needs. (OpenID scopes `openid`, `profile`, `email` etc. are optional add-ons.) Intuit's guidance: only request scopes you actually use. (https://developer.intuit.com/app/developer/qbo/docs/develop/sdks-and-samples-collections/ruby/oauth-ruby-client ; https://developer.intuit.com/app/developer/qbo/docs/develop/authentication-and-authorization/oauth-2.0-playground)

### Token exchange & lifetimes
Exchange `code` at the token endpoint (`POST https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer`). The response fields and lifetimes (from Intuit's own SDK sample response):
- **`access_token`** — `expires_in = 3600` seconds → **1 hour.** Sent as `Authorization: Bearer` on API calls.
- **`refresh_token`** — `x_refresh_token_expires_in = 8726400` seconds → **~101 days.** Used to mint new access tokens.

(https://developer.intuit.com/app/developer/qbo/docs/develop/sdks-and-samples-collections/ruby/oauth-ruby-client)

**Refresh behavior:** exchange the refresh token for a fresh access token before/after the 1-hour expiry. A refresh call can return a **new (rotated) refresh token** — always persist the latest one returned; the ~101-day clock is effectively kept alive by regular refreshes, but a refresh token unused past its window forces a full re-authorization. Ledger-Sync should store the current refresh token durably (single-tenant: one token per install) and update it on every refresh. (Refresh flow via SDK: https://developer.intuit.com/app/developer/qbo/docs/develop/sdks-and-samples-collections/php/authorization)

### Sandbox vs production
- **Different base URLs** for API calls: sandbox `https://sandbox-quickbooks.api.intuit.com` vs production `https://quickbooks.api.intuit.com` (configured as "Development"/"Production" baseUrl in the SDKs).
- **Different key sets** (see registration).
- Test against a **sandbox company** first; the **OAuth 2.0 Playground** can generate an auth code + realm ID + tokens for a sandbox company without building UI. (https://developer.intuit.com/app/developer/qbo/docs/develop/authentication-and-authorization/oauth-2.0-playground ; https://developer.intuit.com/app/developer/qbo/docs/develop/sdks-and-samples-collections/php/authorization)

---

## Q2b — Multi-company / realmID scoping (Book layer)

Confirms the domain model's new **Book** layer (one Organization → several accounting entities, each mapped to its own QuickBooks company).

### API paths are company-scoped via `realmID` — confirmed
Every QBO Accounting API request is scoped to a single company by its **realm ID** embedded in the path: `<baseURL>/v3/company/<realmID>/<resource>` (e.g. `baseURL/company/1234/account`). Intuit's definition: "**Realm ID** identifies a unique, individual QuickBooks Online company file… Realm IDs are specified in the URI of every API request." **Realm ID = company ID** (same number). There is no cross-company API surface — a token used against one realm cannot reach another realm's data. (https://developer.intuit.com/app/developer/qbo/docs/learn/learn-basic-field-definitions ; endpoint form on every entity, e.g. `GET /v3/company/<realmID>/...`: https://developer.intuit.com/app/developer/qbo/docs/api/accounting/report-entities/salesbyclasssummary)

### One app / multiple companies — confirmed; authorization is per-company, and one instance holds many connections concurrently
**There is no one-app-one-company constraint.** A single registered app authorizes **per company**: each `Connect to QuickBooks` / OAuth flow has the user pick *one* company, and returns **one `realmId` with its own access-token + refresh-token set**. To serve several companies, run the authorization once per company and store each **(realmId, access_token, refresh_token)** tuple independently; at call time, select the tuple for the target realmId.

Intuit documents this directly for multi-company apps: "your app needs to be able to **manage multiple QuickBooks company connections**… **Map connections based on each company's `realmID`**… maintain mappings between each `realmID` and the connected company's admin user. **Index using the `realmID`.**" Their worked example has one Intuit identity with three connected companies, each its own realmID. Tokens are per-realm, refreshed independently. (https://developer.intuit.com/app/developer/qbo/docs/go-live/list-on-the-app-store/make-your-app-accountant-ready ; realmId retrieval per authorization: https://developer.intuit.com/app/developer/qbo/docs/learn/learn-basic-field-definitions ; token exchange takes the per-flow realmId: https://developer.intuit.com/app/developer/qbo/docs/develop/sdks-and-samples-collections/php/authorization ; community confirmation of per-company realmId + separate refresh tokens: https://help.developer.intuit.com/s/question/0D54R00008Zz1fUSAR/)

**For Ledger-Sync:** model each **Book** as one QBO connection = one `realmId` + its own token set (and its own CoA mirror + sync state). One Ledger-Sync instance can hold N Books concurrently under a single registered app; the OAuth redirect UI is invoked once per Book to connect it. Note the rate limits in Q5 are **per realmID** (500/min each), so multiple Books scale independently rather than sharing one budget — but the combined 800/min guard and the app-level 10/sec are shared across the app, so heavy fan-out across many Books should still pace per-realm.

---

## Q3 — Reading the Chart of Accounts (the `Account` entity)

**Read/query:** `GET /v3/company/{realmId}/account/{id}` or query `SELECT * FROM Account` at `GET /v3/company/{realmId}/query?query=...`.

### Available fields (key ones for CoA sync)
- `Id` — QBO's stable account identifier (this is what Ledger-Sync stores and puts into `JournalEntry ... AccountRef.value`).
- `Name` — user-defined, must be **unique** within the company.
- `AccountType` — the broad classification (e.g. Bank, Accounts Receivable, Expense, Income, Fixed Asset, Credit Card, …).
- `AccountSubType` — the finer classification (QBO UI "Detail type"). Note: on **create**, either `AccountType` or `AccountSubType` may be specified.
- `Classification` — the top-level bucket: **Asset, Liability, Equity, Revenue, Expense** (derived from AccountType). Queryable, incl. `IN` clause.
- `AcctNum` — account number (if the company uses numbered accounts).
- `CurrencyRef` — currency (relevant for multi-currency companies).
- `Active` — active/inactive flag (see below).
- `SubAccount` (bool), `ParentRef`, `FullyQualifiedName` — hierarchy (`Parent:Child` naming).
- `CurrentBalance`, `CurrentBalanceWithSubAccounts` — running balances.
- `Description`, `SyncToken` (optimistic-concurrency token, required on update), `MetaData` (`CreateTime` / `LastUpdatedTime`).

(Account attributes `Name`/`AccountType`/`AccountSubType`: https://developer.intuit.com/app/developer/qbo/docs/learn/learn-basic-bookkeeping/accounts ; `Classification` queryable with `IN`: https://developer.intuit.com/app/developer/qbo/docs/release-notes/platform-release-notes ; full field list: https://developer.intuit.com/app/developer/qbo/docs/api/accounting/all-entities/account)

### Account types / classifications
`AccountType` + `AccountSubType` are enumerated value sets; `Classification` rolls them up into Asset / Liability / Equity / Revenue / Expense. For a rules engine mapping transactions to accounts, key off `Id` (immutable) and use `AccountType`/`Classification` to constrain which side of the entry an account can legitimately post to. (https://developer.intuit.com/app/developer/qbo/docs/api/accounting/all-entities/account)

### Active / inactive handling (important for a CoA mirror)
- Accounts (like all "name list" entities) are **never hard-deleted** — they are **soft-deleted** by setting `Active = false` (a "delete" via the SDK just flips `Active` to false and updates). (https://developer.intuit.com/app/developer/qbo/docs/develop/sdks-and-samples-collections/php/synchronous-calls)
- **Default query behavior returns active records; to include inactive accounts, filter explicitly:** `SELECT * FROM Account WHERE Active IN (true, false)`. For a faithful CoA mirror, Ledger-Sync should query with `Active IN (true, false)` and store the `Active` flag, rather than relying on the default. (Forum guidance on retrieving inactive accounts: https://help.developer.intuit.com/s/question/0D54R00007y3qZrSAI/ ; query syntax: https://developer.intuit.com/app/developer/qbo/docs/learn/explore-the-quickbooks-online-api/data-queries — *default-vs-explicit ambiguity noted in caveats*)
- **Posting to an inactive account fails:** referencing an inactive account in a JournalEntry yields **error 2500 `Invalid Reference Id`** or **610 `Object Not Found`** ("Something you're trying to use has been made inactive"). Intuit's explicit recommendation: on first connect, cache `Id`, `SyncToken`, `DisplayName`/`Name`, and `Active` for all accounts; keep it fresh via **webhooks** or the **Change Data Capture (CDC)** operation; and validate `Active` before posting. This maps directly onto Ledger-Sync's "QBO is source of truth for CoA" + refresh model. (https://developer.intuit.com/app/developer/qbo/docs/develop/troubleshooting/handling-common-errors)

---

## Q4 — Idempotency (preventing double-posting on retry)

**Yes — QBO has a native client-side idempotency mechanism: the `requestid` query parameter.** This is the primary control for ticket 08.

### `requestid` (native idempotency key)
- Pass `requestid=<unique value>` as a **URI query parameter** on any write (create/update/delete). "This guarantees idempotence. If our service receives another request with the same request ID, instead of performing the operation again or returning an error, it can recognize and send the same response [as] for the original request. This prevents duplication."
- **Uniqueness scope:** the value must be unique across all requests **for a given company (realmId)**.
- **Length:** max **50 characters** for all operations, **except batch** — for batch operations `requestid` max is **36 characters**, and when a `requestid` is combined with per-item batch IDs, only **10 characters** are allowed for each batch ID.
- **Batch nuance:** for a batch retry to be recognized as a replay, **both** the `requestid` **and** each batch ID must match the original; same `requestid` but different batch IDs → treated as a different (new) request.
- Intuit recommends generating the value with a UUID/GUID library.

(Authoritative source — "Request ID" section: https://developer.intuit.com/app/developer/qbo/docs/learn/learn-basic-field-definitions ; help article: https://help.developer.intuit.com/s/article/What-is-RequestId-and-its-usage)

**For Ledger-Sync:** generate one stable `requestid` (UUID) **per Journal Entry at first post attempt, persist it on the entry record, and reuse the same value on every retry.** That alone makes retries idempotent server-side. This is the recommended backbone of the per-entry sync-status + idempotent-retry design.

### Complementary detection mechanisms (defense in depth / for gaps)
Because `requestid` replay windows are not guaranteed indefinite, pair it with:

1. **Stable, client-generated `DocNumber`** — set a meaningful stable DocNumber (e.g. Ledger-Sync's internal entry ID) on the JE. Caveat: DocNumber alone does **not** deduplicate — if you **omit** it and the company has "Automatic Transaction Numbers" (the default), QBO assigns a new sequential number to **every** create (including retries) and creates a duplicate; there is **no dedup on DocNumber by default**. So DocNumber is a correlation/lookup key, not an idempotency guarantee. (https://developer.intuit.com/app/developer/qbo/docs/learn/learn-basic-field-definitions)
2. **Query-before-write on uncertain failures** — Intuit's recommended retry pattern: on a **network error or 5xx** (outcome unknown), **query first** (e.g. by DocNumber, or `PrivateNote`) before retrying; on a **4xx validation error**, the txn was **not** created, so fix and retry safely. (https://developer.intuit.com/app/developer/qbo/docs/learn/learn-basic-field-definitions)
3. **`PrivateNote`** — a free-text field on the JE where Ledger-Sync can stamp its own entry ID for after-the-fact correlation/lookup (not uniqueness-enforced by QBO, but queryable).

**Recommended stack for ticket 08:** persistent per-entry `requestid` (UUID) **+** stable `DocNumber`/`PrivateNote` carrying Ledger-Sync's entry ID **+** query-before-retry on ambiguous (5xx/network) failures.

---

## Q5 — Rate limits & batch endpoints

### Rate limits (REST API)
**Production:**
- **500 requests / minute per realmId.**
- **10 requests / second per realmId + app.**
- If the app also calls non-QBO Intuit endpoints, a **combined 800 requests / minute per realmId + app.**

**Sandbox:** same 500/min per realm and 10/sec per realm+app, plus **40 emails/day per realm**.

**Throttling behavior:**
- Exceeding a limit returns **HTTP 429** → **wait 60 seconds before retrying.**
- Any request running longer than **120 seconds times out** (budget batch sizes accordingly).
- Max **1000 entities per query response** → use **pagination** (`STARTPOSITION` / `MAXRESULTS`) to page through larger sets (relevant when reading a large Chart of Accounts).

(https://developer.intuit.com/app/developer/qbo/docs/learn/limits-and-throttles)

### Batch endpoint (reads and writes in one call)
- **Endpoint:** `POST /v3/company/{realmId}/batch`.
- A single batch bundles **mixed operations** — `create`, `update`, `delete`, **and `query`** — each tagged with a caller-supplied unique batch ID (`bId`).
- **Recommended max 30 payloads per batch request.**
- **Batch throttles:** **40 batch requests / minute per realmId + app**, and **120 batch requests / minute per realmId**.
- Combine with `requestid` for idempotent batch retries (see Q4 batch nuance: both `requestid` and each `bId` must match to replay).

(Batch operations model: https://developer.intuit.com/app/developer/qbo/docs/develop/sdks-and-samples-collections/net/synchronous-calls ; batch limits/throttles: https://developer.intuit.com/app/developer/qbo/docs/learn/limits-and-throttles)

**For Ledger-Sync:** posting one JE per API call is well within 10/sec and 500/min. If bulk-posting many entries, the batch endpoint (≤30 JE creates per call, ≤40 batch/min) cuts round-trips — but keep per-JE `requestid`+`bId` stable so a batch retry replays rather than duplicates. Reading the CoA is one `SELECT * FROM Account WHERE Active IN (true,false)`, paginated at 1000/page.

---

## Open questions / caveats

1. **Query default for `Active`:** sources conflict on whether an *unfiltered* `SELECT * FROM Account` returns active-only or active+inactive. The existence of Intuit forum threads on "how to get inactive accounts" (answer: use `WHERE Active IN (true, false)`) implies the default is **active-only**, but one secondary summary claimed omitting `WHERE` returns both. **Resolution: don't rely on the default — always query `Active IN (true, false)` for a full CoA mirror.** Worth a 5-minute sandbox check to confirm the exact default. (https://developer.intuit.com/app/developer/qbo/docs/learn/explore-the-quickbooks-online-api/data-queries)
2. **`requestid` replay retention window:** the docs guarantee idempotent replay but do **not** state how long QBO retains a `requestid`→response mapping. Treat `requestid` as reliable for prompt retries; for long-delayed retries, back it with query-before-write. Confirm the window with Intuit developer support if long retry gaps are expected.
3. **JournalEntry cash/accrual nuance:** JEs post to the GL and are accepted regardless of company accounting method, but *which* basis a given line surfaces on in reports can depend on the accounts used (e.g. A/R–A/P lines behave differently cash vs accrual). This affects reporting, not the ability to post. Verify against the JournalEntry reference's `Line`/`Adjustment` notes if report-basis fidelity matters. (https://developer.intuit.com/app/developer/qbo/docs/api/accounting/all-entities/journalentry)
4. **JournalEntry exact required-field minimality & minorversion:** the precise required-field set and line minimums were confirmed via the error model (2300/2500), the linked-transactions doc, and forum posts; the canonical JournalEntry reference page is JS-rendered and resisted automated fetch. Recommend a direct human read of https://developer.intuit.com/app/developer/qbo/docs/api/accounting/all-entities/account and .../journalentry (and pinning a `minorversion`) before finalizing the JE builder in ticket 06.
5. **Refresh-token rotation specifics:** the ~101-day lifetime is from Intuit's SDK sample response; exact rotation cadence (Intuit historically rotates refresh tokens roughly daily and enforces a ~100-day absolute window) should be confirmed against the current auth docs so Ledger-Sync's token-refresh job persists every rotated token. (https://developer.intuit.com/app/developer/qbo/docs/develop/authentication-and-authorization/faq)
