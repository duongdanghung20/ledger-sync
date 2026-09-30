"""Categorization feature (ticket 09).

Automatic + manual assignment of each Imported Transaction to exactly one target
Account. Bookkeeper-authored Categorization Rules match payee/description
(contains/equals, case-insensitive) and amount/date (gte/lte/between); conditions
AND within a rule, OR is a second rule. Rules evaluate first-match-wins by priority,
so each transaction gets one target Account. A manual override beats any rule and
survives re-running. A rule whose target Account went inactive is kept, flagged
invalid, and skipped at match time (the transaction falls through to manual).

Public seam for tickets 10/12 (import from ``app.features.categorization``)::

    # Pure engine — no DB, table-driven testable:
    from app.features.categorization import (
        match_condition,      # match_condition(txn, condition) -> bool
        categorize_one,       # categorize_one(txn, rules) -> {account_id, source, rule_id}
        categorize,           # categorize(txns, rules, overrides) -> [{txn, account_id, source, rule_id}]
        validate_conditions,  # validate_conditions(conditions) -> None (raises RuleError)
        RuleError,
    )

    # DB operations:
    from app.features.categorization import (
        run_categorization,   # run_categorization(conn, book_id) -> {total, by_rule, manual, uncategorized}
        set_manual_category,  # set_manual_category(conn, book_id, transaction_id, qbo_account_id) -> dict | None
        clear_category,       # clear_category(conn, book_id, transaction_id) -> dict | None
        revalidate_rules,     # revalidate_rules(conn, book_id) -> int (rows re-flagged)
        list_rules,           # list_rules(conn, book_id) -> [rule]
        valid_target_ids,     # valid_target_ids(conn, book_id) -> set[str]
    )
"""

from .engine import (
    RuleError,
    categorize,
    categorize_one,
    match_condition,
    validate_conditions,
)
from .service import (
    clear_category,
    list_rules,
    revalidate_rules,
    run_categorization,
    set_manual_category,
    valid_target_ids,
)

__all__ = [
    "match_condition",
    "categorize_one",
    "categorize",
    "validate_conditions",
    "RuleError",
    "run_categorization",
    "set_manual_category",
    "clear_category",
    "revalidate_rules",
    "list_rules",
    "valid_target_ids",
]
