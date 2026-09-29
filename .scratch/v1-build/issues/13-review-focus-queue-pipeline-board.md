# 13: Review — Focus queue, Pipeline board, view switching

**What to build:** The two alternative review layouts and free movement between all three. The Focus queue shows one transaction at a time with the full debit/credit preview and balance proof and keyboard approval, so a Bookkeeper can review the money path closely when they want to. The Pipeline board shows a lane per state with per-card or whole-lane advance, so the queue can be worked as a state machine. A Bookkeeper switches freely between the Ledger table, the Focus queue, and the Pipeline board over the same data and the same operations, and the four operations (assign/override, approve, push, retry) behave identically in every view, so layout is a preference, not a different feature. The chosen view is remembered across sessions.

Frontend work uses the frontend-design skill. `preferred_review_view` defaults to a `localStorage` key (zero migration); add a `User.preferred_review_view` column only if a server-remembered preference is worth the migration field.

**Blocked by:** 12.

**Status:** ready-for-agent

- [ ] The Focus queue shows one transaction at a time with a full debit/credit preview, balance proof, and keyboard approval.
- [ ] The Pipeline board shows a lane per state with per-card and whole-lane advance.
- [ ] A Bookkeeper switches freely between all three views over the same data and operations; the four operations behave identically in each.
- [ ] The chosen view is remembered across sessions.
