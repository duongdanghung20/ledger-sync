# 04: QuickBooks client port + in-memory fake

**What to build:** *(Prefactor.)* The one adapter that wraps every outbound QuickBooks Online HTTP call, so no other module ever touches `httpx` directly and every later money-path test can run against a controllable fake. The port exposes token refresh, an Account query returning the full mirror set (active and inactive), a Journal Entry POST that carries a `requestid`, and a query-for-a-Journal-Entry-by-`DocNumber`; it surfaces the HTTP outcome class (2xx / 4xx / 5xx / network / 429) to the caller so callers can classify retries. Ship the real httpx-backed implementation and an in-memory fake used throughout the test suite: the fake honours `requestid` replay (same `requestid` returns the same result and creates no duplicate), answers query-by-`DocNumber`, serves the Account query, refreshes tokens, and can be driven to return any status class on demand. QuickBooks app credentials come only from `QBO_CLIENT_ID` / `QBO_CLIENT_SECRET` in the environment. This seam is what makes the idempotency and retry tests in ticket 11 possible; building it once, early, keeps 05/06/11 easy.

**Blocked by:** 01.

**Status:** ready-for-agent

- [ ] The port exposes `refresh_token(connection)`, `query_accounts(connection)` returning accounts with `Active IN (true, false)`, `post_journal_entry(connection, entry, requestid)`, and `find_journal_entry_by_doc_number(connection, doc_number)`.
- [ ] The port returns the HTTP outcome class (2xx / 4xx / 5xx / network / 429) to the caller rather than swallowing it.
- [ ] No module outside this adapter calls `httpx` for QuickBooks (grep-verifiable).
- [ ] The in-memory fake: same `requestid` → same response with no second Journal Entry created; supports query-by-`DocNumber`; serves `query_accounts`; refreshes tokens; and can be told to return 4xx / 5xx / network / 429 on demand.
- [ ] QuickBooks credentials are read only from the environment, never persisted to the database or shown in the UI.
