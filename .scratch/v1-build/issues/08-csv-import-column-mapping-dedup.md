# 08: CSV import, Column-Mapping Profile, per-Bank-Account dedup

**What to build:** The Bookkeeper's path from an arbitrary bank CSV to canonical, de-duplicated Imported Transactions. A Bookkeeper uploads any bank's CSV; a saved per-bank Column-Mapping Profile maps that bank's columns to the canonical fields and is auto-suggested from the uploaded file's header signature, so the Bookkeeper usually just confirms. A profile supports both bank conventions — a single signed amount column (with an optional sign flip) or separate debit/credit columns — and dot- or comma-decimal formats. The Bookkeeper picks or creates the source Bank Account at import time (defaulted by the profile), so every Imported Transaction carries its Bank Account. Rows parse into Imported Transactions with amounts as two-decimal-place Decimals (never float). Dedup is scoped per Bank Account: if the bank supplies a stable unique id, dedup uses it with certainty; otherwise it uses a content multiset over (date, signed amount, description, payee) so overlapping re-imports never double-import while two genuinely identical same-day transactions are both kept. Malformed or partial rows collect into a rejected list with a reason while the valid rows still import; possible in-file duplicates surface as a count for the Bookkeeper to review rather than being silently decided.

This is a money-path slice held to the strict correctness bar. The CSV parse + multiset dedup engine is lifted from the ticket-05 prototype (its headless dedup corpus already passes 7/7 and seeds this ticket's tests).

**Column-Mapping Profile shape** (validated against three real sample layouts; encodes the sign-convention decision):

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

**Blocked by:** 07.

**Status:** ready-for-agent

- [ ] A Column-Mapping Profile persists per Book and is auto-suggested from the uploaded file's header signature; the Bookkeeper can confirm or pick another.
- [ ] Both amount conventions import correctly: single signed column (optional flip) and separate debit/credit columns; dot and comma decimals both parse.
- [ ] The source Bank Account is chosen or created at import (defaulted by the profile); every Imported Transaction carries its Bank Account; amounts are stored as 2dp Decimal.
- [ ] Dedup is per Bank Account: with `external_id` present, on `(bank_account_id, external_id)`; otherwise a content multiset over (date, signed amount, description, payee).
- [ ] **Money-path:** an overlapping re-import never double-imports, and two genuinely identical same-day transactions are both kept; the same date/amount/description in two different Bank Accounts stays two distinct transactions.
- [ ] Malformed or partial rows collect into a rejected list with reasons while valid rows still import; nothing is silently dropped.
- [ ] Possible in-file duplicates surface as a count to review, not silently decided.
