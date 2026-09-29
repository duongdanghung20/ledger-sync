# 06: Chart of Accounts mirror

**What to build:** A read-only mirror of the Book's QuickBooks Chart of Accounts, so a Bookkeeper categorizes against the company's real accounts and Ledger-Sync never writes accounts back. The mirror is fetched on connect, refreshable on demand, and lazily refreshed before a Bookkeeper reviews or pushes, so a stale mirror never blocks or corrupts a post. Refresh reconciles by upserting on `qbo_id`: accounts renamed, retyped, or deactivated in QuickBooks are updated in place, and an account absent from QuickBooks is marked inactive rather than hard-deleted, so historical references still resolve and a deactivated account does not silently reappear. The mirror includes inactive accounts. Categorization-target selection offers only active Accounts, grouped by Classification, and excludes Accounts-Receivable and Accounts-Payable account types (which QuickBooks would reject without a customer/vendor reference). Each Book shows when its Chart of Accounts was last synced.

**Blocked by:** 05.

**Status:** ready-for-agent

- [ ] On connect, the Book's full Chart of Accounts (active and inactive) is mirrored read-only; Ledger-Sync never writes an Account back to QuickBooks.
- [ ] A manual refresh and a lazy refresh before review/push both reconcile the mirror.
- [ ] Reconcile upserts by `qbo_id`: renames and retypes update in place; an account absent from QuickBooks becomes inactive and is never hard-deleted; a returning account is not duplicated.
- [ ] Categorization-target selection returns only active Accounts, grouped by Classification, and excludes Accounts-Receivable and Accounts-Payable types.
- [ ] Each Book exposes its last-synced timestamp.
