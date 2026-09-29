# Review and approval UI

Type: prototype
Status: resolved
Blocked by: 04, 06

## Question

Design the human review/approve surface — the screen where a Bookkeeper checks categorized transactions before they post to QuickBooks Online.

- Flow: view imported + categorized transactions → adjust an Account → approve → push.
- The states a transaction moves through in the UI (imported → categorized → approved → posted) and how Sync Status is shown.
- Bulk vs per-row approval.
- Prototype the review screen using the `frontend-design` skill.

HITL prototype; keep it on record and link it here.

## Answer

**Ship all three review layouts as user-switchable views over one shared review surface** — the Jira/Asana/Trello pattern (list vs. board vs. detail), not a single winning layout. The three variants are not competing designs to choose between; they are three renderers over the *same* review dataset and the *same* action set, and the User picks which one they work in.

Prototype (primary source, all three variants, switch via bottom bar or `?variant=A|B|C`): [`09-review-approval-prototype.html`](09-review-approval-prototype.html). In-memory, clickable; the money-path preview (`journalLines`) is verified balanced and sign-correct against the ticket 06 rule (outflow → Dr category / Cr cash; inflow → Dr cash / Cr category; `A=0` rejected).

### The three views

- **Ledger table** (`A`) — dense one-row-per-transaction grid; state-filter tabs with live counts; inline Account `<select>` per row; checkbox multi-select driving a sticky bulk bar (**Approve selected → Push selected**). Bulk-first. Sync Status shown as an inline pill per row; a failed row shows its `last_error` inline with a Retry action. **This is the default view.**
- **Focus queue** (`B`) — one transaction at a time with the full **Journal Entry Dr/Cr preview and balance proof** rendered before approval; keyboard-driven (`⏎` approve-and-next, `→` skip). Per-row, deliberate — the closest read on the money path. Posted/failed surface as rail counts.
- **Pipeline board** (`C`) — kanban, one lane per state (Uncategorised → Categorised → Approved → Posted → Failed); advance a single card or a whole lane. State-machine-first. The Failed lane is always visible with per-card Retry.

### Rulings (bind every view — a view is presentation only, never its own state model)

1. **One shared model + one operation set.** All views render the same Book-scoped list of Imported Transactions and call the same four operations: assign/override Account, approve, push, retry. Views differ only in layout, density, and primary affordance. This is one feature with three skins, not three features — the operations, permission checks, and state transitions live below the view layer, not inside it.
2. **View is a per-User preference, persisted, remembered across sessions.** Recommended: one nullable column on `User` (e.g. `preferred_review_view` ∈ {`table`,`queue`,`board`}), defaulting to `table`. It is a UI preference only — it grants nothing and touches no financial data, so it sits outside RBAC. (`ponytail:` a browser `localStorage` key is the even-lazier option with zero backend change; chose the `User` column so the preference follows the person across devices, at the cost of one migration field. Flip to `localStorage` if that field isn't worth a migration.)
3. **The state model is identical in every view:** `imported → categorized → approved → posted`, plus a `failed` Sync Status. "Categorised" = an Account is assigned (by rule per ticket 04, or by manual override). No view may invent or reorder states.
4. **Approve materialises the balanced 2-line Journal Entry** (ticket 06); it is offered only for a transaction that has an Account (an uncategorised transaction cannot be approved from any view). **Push is the idempotent single-entry post** (ticket 08); a `pending` entry can be un-approved back to `categorized` (06); a `posted` entry is immutable.
5. **Failure is always reachable.** Whatever the view, a `failed` entry surfaces its `last_error`/`last_error_code` and offers manual re-push (ticket 08). The Ledger table shows it inline, the Pipeline board gives it a permanent lane, the Focus queue counts it in the rail and routes to it.
6. **The Journal Entry Dr/Cr preview is a shared element**, reachable from every view (row-expand in the table, card detail on the board), and foregrounded by default only in the Focus queue. Approving without the reviewer having *seen* the debits and credits is allowed from the table/board (they trust the sign rule); the Focus queue exists for when they want to look.
7. **Bulk vs. per-row is answered "both, by view", not globally:** Ledger table = checkbox bulk approve/push; Focus queue = one at a time; Pipeline board = per-card or whole-lane batch. All three drive the same underlying per-entry operations, so a batch is just a loop over the single-entry push — no separate batch push path (consistent with ticket 08's "one per call, synchronous").
8. **Scope guard:** switching views is a client-side layout swap over already-loaded data; it is not a new fetch surface or a new permission boundary. Roles stay as ticket 07 (approve/push shared by Admin + Bookkeeper).
