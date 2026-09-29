# 07: Bank Account creation + one-to-one mapping

**What to build:** The Admin's setup of each real-world Bank Account and its ledger counterpart. An Admin creates each Bank Account (checking, card) in a Book and maps it one-to-one to its cash-side QuickBooks Account, chosen from the mirrored Chart of Accounts. The mapping is required, so every Journal Entry later built from that Bank Account's transactions has a defined counter-side. Creating and mapping Bank Accounts is Admin-only. A setup screen lists Bank Accounts and their mapped Accounts.

**Blocked by:** 06.

**Status:** ready-for-agent

- [ ] An Admin creates a Bank Account scoped to the Book and maps it to exactly one active QuickBooks Account from the mirror; the mapping (`qbo_account_id`) is required and cannot be left empty.
- [ ] Bank Account creation and mapping routes and UI are Admin-only; a Bookkeeper is rejected server-side.
- [ ] A setup screen lists each Bank Account with its mapped cash-side Account.
