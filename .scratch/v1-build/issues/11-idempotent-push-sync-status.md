# 11: Idempotent push + Sync Status

**What to build:** Pushing approved Journal Entries to QuickBooks so that a retry after any failure never double-posts. Each approved entry pushes one at a time, synchronously, giving per-entry success/failure. The `requestid` minted at approval is reused on every retry and never regenerated, so QuickBooks's native idempotency collapses a duplicate push into the original entry. A crash mid-push is recovered on the next push by querying QuickBooks by `DocNumber`: an ambiguous 5xx or network failure is resolved to posted or failed rather than blindly re-posted. Pre-push validation skips an entry with an inactive target Account or an unmapped Bank Account — marking it failed with the right code — while the rest of the batch continues, so one bad entry never blocks the batch. A 429 rate-limit response stops the push and leaves the remaining entries pending with a "retry shortly" message, so already-posted entries stay posted and nothing is lost. A 4xx is failed and safe to re-push after a fix. Concurrent pushes on the same Book are serialized with a PostgreSQL advisory lock so two clicks or two people cannot race the same entries. Each entry exposes its Sync Status, last error and error code, last attempt time, and attempt count, with a safe manual retry available.

This is a money-path slice held to the strict correctness bar; the fake QuickBooks client (ticket 04) is driven through every retry branch.

**Blocked by:** 10, 05.

**Status:** ready-for-agent

- [ ] Each approved entry pushes one at a time, synchronously, with per-entry success/failure; a connection that cannot refresh blocks the push.
- [ ] **Money-path:** a retry replays the same `requestid` and returns the original entry — no duplicate in QuickBooks.
- [ ] **Money-path:** a crash mid-push is recovered on the next push via query-by-`DocNumber`, resolving an ambiguous 5xx/network to posted or failed with no duplicate.
- [ ] Pre-push validation skips an entry with an inactive target Account or an unmapped Bank Account as failed with the specific code, and the rest of the batch continues posting.
- [ ] A 429 stops the push and leaves the remaining entries pending with a "retry shortly" message; already-posted entries stay posted.
- [ ] A 4xx marks the entry failed and it is safe to re-push after the fix.
- [ ] Concurrent pushes on the same Book are serialized by a PostgreSQL advisory lock.
- [ ] Each entry surfaces Sync Status, last error + `last_error_code`, `last_attempt_at`, and `attempt_count`; manual retry is available and safe.
