# 12: Review core — read models, operations, Ledger table, happy-path e2e

**What to build:** The default review surface and the single operation set every view shares. The four operations — assign/override Account, approve, push, retry — are implemented once behind read models, so presentation is layout, not a different feature, and switching views (ticket 13) never changes what an action does. Transaction state is derived, not a second stored column: *uncategorized* = no Account assigned; *categorized* = Account assigned; *approved* = a pending Journal Entry exists; *posted* / *failed* = the Journal Entry's Sync Status. The default view is a dense Ledger table with state-filter tabs, inline Account pickers, and checkbox bulk approve/push, so a Bookkeeper can process a batch quickly. Approval is barred for an uncategorized transaction. A failed entry surfaces its error and offers a manual retry. A Playwright end-to-end test covers the happy path over a running stack with a fake QuickBooks: import → categorize → approve → push.

Frontend work uses the frontend-design skill.

**Blocked by:** 09, 10, 11.

**Status:** ready-for-agent

- [ ] The four operations (assign/override Account, approve, push, retry) exist once as shared operations over read models; transaction state is derived (no stored status column that can drift).
- [ ] The default Ledger table shows state-filter tabs, inline Account pickers, and checkbox bulk approve/push.
- [ ] Approval is barred for an uncategorized transaction (no Account assigned).
- [ ] A failed entry surfaces its error and a manual retry in the table.
- [ ] A Playwright e2e test covers import → categorize → approve → push against a fake QuickBooks on a running Next.js + FastAPI stack.
