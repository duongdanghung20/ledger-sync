# 10: Double-entry Journal Entry materialization

**What to build:** Turning an approved categorized transaction into exactly one balanced two-line Journal Entry, correct by construction. On approval, a transaction materializes one Journal Entry whose two lines are reconstructed from a stored snapshot — there is no Journal Entry lines table. The sign rule is account-type-agnostic: an outflow debits the category Account and credits cash, an inflow debits cash and credits the category Account, both posting `abs(amount)` with opposite `PostingType`, so debits always equal credits and QuickBooks error 2300 is structurally impossible. Transfers, refunds, and card paydowns need no special case. A zero-amount transaction is rejected rather than turned into an empty entry. A transaction whose Bank Account is unmapped is blocked from journal build and flagged, never silently dropped or posted wrong. Amounts are two-decimal-place Decimals end to end, never float. The entry snapshots its accounts and amount so it is immutable to later rule re-runs or re-categorization; the transaction's description/payee is carried into the memo. A `requestid` is minted at approval and reused forever after; a short per-Book monotonic `doc_number` is assigned (the full id also goes in the memo/PrivateNote). Approve materializes the entry (Sync Status `pending`); un-approve discards a still-pending entry back to categorized, so a Bookkeeper can revise before posting but not after. A debit/credit preview is available before approval.

This is a money-path slice held to the strict correctness bar. The sign rule is validated in the ticket-09 prototype.

**Sign rule** — for signed canonical amount `A` (negative = outflow of the Bank Account):

```
A < 0 (outflow):  Debit category |A|,  Credit cash     |A|
A > 0 (inflow):   Debit cash     |A|,  Credit category |A|
A = 0:            rejected (no zero-amount Journal Entry)
```

**Journal Entry** — a thin entity, one-to-one with the Imported Transaction, materialized at approval, storing a snapshot plus sync fields:

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
  last_error; last_error_code; last_attempt_at; attempt_count   // used by ticket 11
}
```

**Blocked by:** 09, 07.

**Status:** ready-for-agent

- [ ] Approving a categorized transaction materializes exactly one Journal Entry whose two lines are reconstructed from a snapshot; no lines table exists.
- [ ] **Money-path (balance & sign):** every materialized entry has debits equal to credits; an outflow debits category / credits cash and an inflow debits cash / credits category, both `abs(A)`; `A = 0` is rejected.
- [ ] A transaction whose Bank Account is unmapped is blocked from journal build and flagged.
- [ ] Amounts are 2dp Decimal end to end (never float); the memo carries description/payee.
- [ ] The entry snapshots accounts + amount and does not change when rules are re-run or the transaction is re-categorized.
- [ ] A `requestid` is minted at approval and never regenerated; a per-Book monotonic `doc_number` is assigned.
- [ ] Un-approve discards a still-pending entry back to categorized; a posted entry cannot be un-approved.
- [ ] A debit/credit preview is reachable before approval.
