# 09: Categorization engine, rules, and manual override

**What to build:** Automatic and manual assignment of each Imported Transaction to exactly one target Account. A Bookkeeper writes Categorization Rules matching payee and description (contains, equals, case-insensitive) and amount and date (gte, lte, between); conditions within a rule are AND-ed and OR is expressed as a second rule, keeping the model flat and predictable. Rules evaluate first-match-wins by priority so each transaction gets exactly one target Account and the journal has one clean counter-side; overlapping rules are resolved by a silent priority tiebreak. Amount conditions match the signed canonical amount (negative = outflow), so "purchases of $100+" is unambiguous. A Bookkeeper can manually categorize any uncategorized transaction, and a manual override beats rules and survives re-running categorization, so a deliberate choice is never clobbered. Each transaction records its source (rule, manual, or none) and which rule matched, so a Bookkeeper can trace why it landed where it did. A rule whose target Account has gone inactive is kept, flagged invalid, and skipped at match time, so the transaction falls through to manual rather than categorizing onto an account QuickBooks would reject.

The pure matcher (`matchCondition`, `categorizeOne`, `categorize`) is lifted from the ticket-04 prototype and exercised directly with table-driven unit tests, which are cheaper than HTTP round-trips for the dense combinatorial cases.

**Blocked by:** 08, 06.

**Status:** ready-for-agent

- [ ] Rules match payee/description via contains / equals / case-insensitive and amount/date via gte / lte / between; conditions within a rule AND; OR is a separate rule.
- [ ] Rules evaluate first-match-wins by priority; each transaction receives exactly one target Account; overlapping rules resolve by silent priority tiebreak.
- [ ] Amount conditions match the signed canonical amount (negative = outflow).
- [ ] A manual categorization or override beats any rule and survives re-running categorization.
- [ ] Each transaction records `category_source ∈ {rule, manual, none}` and the matched rule id.
- [ ] A rule whose target Account is inactive is kept, flagged invalid, and skipped at match time; the transaction falls through to manual.
- [ ] Table-driven unit tests exercise the lifted matcher directly across the combinatorial cases.
