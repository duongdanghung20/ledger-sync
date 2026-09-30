"""Pure categorization engine — lifted from the ticket-04 prototype.

No DB, no globals: the bit the spec validated and the real code adopts, so it stays
table-driven-testable (tickets 09/10 exercise it directly). The prototype's JS
``matchCondition``/``categorizeOne``/``categorize`` map 1:1 onto the functions here;
the only adaptations are the condition key ``op`` -> ``operator`` (ticket schema) and
ordered comparisons made type-aware so a signed ``Decimal`` amount and a ``date`` both
compare correctly instead of via JS ``Number()``.

A condition is ``{field, operator, value, value2?}`` where:
  * text fields  (payee, description) -> ``contains`` | ``equals`` (case-insensitive)
  * ordered fields (amount, date)     -> ``gte`` | ``lte`` | ``between`` (value2 for the upper bound)

Amount is the SIGNED canonical amount (negative = outflow), so "purchases of $100+"
is ``amount lte -100``. Conditions within a rule are AND-ed; OR is a separate rule.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

TEXT_FIELDS = ("payee", "description")
ORDERED_FIELDS = ("amount", "date")
TEXT_OPS = ("contains", "equals")
ORDERED_OPS = ("gte", "lte", "between")


class RuleError(ValueError):
    """A rule's conditions are malformed (raised at the API boundary -> 422)."""


def _coerce(field_value: Any, operand: Any) -> Any:
    """Coerce a rule operand (JSON scalar) to the field value's type so ordered
    comparison is apples-to-apples: Decimal for amounts, date for dates."""
    if isinstance(field_value, Decimal):
        return Decimal(str(operand))
    if isinstance(field_value, date):
        return date.fromisoformat(str(operand))
    return operand


def match_condition(txn: Mapping[str, Any], cond: Mapping[str, Any]) -> bool:
    """One condition against one transaction. Missing value or a non-comparable
    operand is a non-match, never an error."""
    value = txn.get(cond["field"])
    op = cond["operator"]
    if value is None:
        return False
    if op == "contains":
        return str(cond["value"]).lower() in str(value).lower()
    if op == "equals":
        return str(value).lower() == str(cond["value"]).lower()
    try:
        lo = _coerce(value, cond["value"])
        if op == "gte":
            return value >= lo
        if op == "lte":
            return value <= lo
        if op == "between":
            return lo <= value <= _coerce(value, cond["value2"])
    except (InvalidOperation, ValueError, TypeError):
        return False
    return False


def categorize_one(txn: Mapping[str, Any], rules: Iterable[Mapping[str, Any]]) -> dict:
    """First-match-wins by ascending priority. Rules flagged ``invalid_target``
    (target account gone inactive) are skipped, mirroring the prototype's inactive
    filter. Returns ``{account_id, source, rule_id}``."""
    ordered = sorted(
        (r for r in rules if not r.get("invalid_target")),
        key=lambda r: r["priority"],
    )
    for rule in ordered:
        conds = rule.get("conditions") or []
        # Empty conditions must never match everything (JS [].every() is true).
        if conds and all(match_condition(txn, c) for c in conds):
            return {"account_id": rule["target_qbo_account_id"], "source": "rule", "rule_id": rule["id"]}
    return {"account_id": None, "source": "none", "rule_id": None}


def categorize(
    txns: Iterable[Mapping[str, Any]],
    rules: Iterable[Mapping[str, Any]],
    overrides: Mapping[Any, Any],
) -> list[dict]:
    """Categorize each transaction. A manual ``overrides[txn_id]`` (a target account
    id) beats any rule. Returns one ``{txn, account_id, source, rule_id}`` per txn."""
    rules = list(rules)
    results = []
    for t in txns:
        if overrides.get(t["id"]) is not None:
            results.append({"txn": t, "account_id": overrides[t["id"]], "source": "manual", "rule_id": None})
        else:
            results.append({"txn": t, **categorize_one(t, rules)})
    return results


def validate_conditions(conditions: Any) -> None:
    """Trust-boundary check for a rule's conditions. Raises RuleError so a broken
    rule can never be saved and later match nothing (or everything)."""
    if not isinstance(conditions, list) or not conditions:
        raise RuleError("A rule needs at least one condition.")
    for c in conditions:
        if not isinstance(c, dict):
            raise RuleError("Each condition must be an object.")
        field, op = c.get("field"), c.get("operator")
        if field in TEXT_FIELDS:
            if op not in TEXT_OPS:
                raise RuleError(f"{field} supports {TEXT_OPS}, not {op!r}.")
        elif field in ORDERED_FIELDS:
            if op not in ORDERED_OPS:
                raise RuleError(f"{field} supports {ORDERED_OPS}, not {op!r}.")
        else:
            raise RuleError(f"Unknown field {field!r}.")
        if c.get("value") in (None, ""):
            raise RuleError("A condition needs a value.")
        if op == "between" and c.get("value2") in (None, ""):
            raise RuleError("A 'between' condition needs a second value.")
