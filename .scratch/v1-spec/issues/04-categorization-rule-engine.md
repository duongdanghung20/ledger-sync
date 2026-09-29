# Categorization rule engine

Type: prototype
Status: resolved
Blocked by: 03

## Question

Design the deterministic categorization rule engine and the manual-override path.

- Rule shape: which Imported Transaction fields can be matched (payee, description, amount, date), which operators (contains, equals, range), and the target Account.
- Ordering/priority: first-match wins vs collecting multiple matches. What happens on conflict.
- Unmatched transactions: how they surface for manual categorization.
- Prototype the match behaviour against sample rows to confirm the rule model feels right before it's specced.

HITL prototype; keep the prototype on record and link it here.

## Answer

Prototype: [`04-categorization-prototype.html`](04-categorization-prototype.html) (self-contained; open by double-click). Verdict from the prototype session: **LGTM** — the first-cut model and all three open rulings accepted as built. Repo is not under git, so the prototype is kept in place as the primary source rather than on a throwaway branch.

**Categorization Rule model (v1, Book-scoped, deterministic):**

- A **Categorization Rule** is an ordered set of **conditions** plus one target **Account** (a QuickBooks ledger Account from the Book's mirrored Chart of Accounts). Target set = all `Active` accounts excluding AR/AP, per ticket 03.
- **Condition** = `{ field, operator, value[, value2] }`.
  - Matchable fields: `payee`, `description`, `amount`, `date`.
  - Operators: `contains`, `equals` on text (case-insensitive); `gte`, `lte`, `between` on `amount`/`date`.
- **Within a rule, conditions are AND-ed.** OR is expressed as a second rule — the model stays flat, no nested boolean tree. (Confirmed: no in-rule OR for v1.)
- **Matching is first-match-wins by priority.** Rules evaluate in ascending priority order; the first rule whose conditions all match assigns its Account and evaluation stops. Guarantees **exactly one Account per categorized Imported Transaction** — the clean single counter-side that ticket 06 needs to build one balanced Journal Entry.

**Three rulings (accepted):**

1. **Amount matching runs on the signed canonical amount** (negative = money leaving the Bank Account). "Purchases of $100+" is written `amount ≤ -100`. Not magnitude/absolute.
2. **Conflict handling is a silent priority tiebreak.** When a transaction matches two or more rules, priority order decides silently; v1 does not surface "also matched rule X". First-match-wins is the whole conflict story.
3. **Manual override sets the Account on the single Imported Transaction only.** "Make this a rule" from an override is **deferred** (ponytail — add later if bookkeepers ask).

**Manual override / unmatched path:**

- A transaction matching no rule is **`uncategorized`** and surfaces for human categorization.
- A **manual override** sets the Account directly on that one transaction, **beats any rule, and survives re-running categorization** (re-run never clobbers a manual choice). This is the manual-override path the domain requires; it applies both to uncategorized rows and to overriding a rule the bookkeeper disagrees with.
- Each categorized transaction records its **source** (`rule` / `manual` / `none`) and, for rule matches, which rule matched — for traceability in the review/approval UI (ticket 09).

**Dangling-reference handling (inherited from ticket 03):** a rule whose target Account has gone inactive is kept, flagged, and skipped (transaction falls through to manual); enforced at push time (ticket 08).

**Liftable engine** (validated, pure — lives in the prototype, adopted by the real code): `matchCondition(txn, condition)`, `categorizeOne(txn, rules)`, `categorize(txns, rules, overrides)`.
