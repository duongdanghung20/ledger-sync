"""Categorization DB layer: rule CRUD, the run that applies rules (preserving manual
overrides), manual assign/clear, and target re-validation against the Accounts mirror.

The matching itself is the pure engine (``engine.py``); this module only moves data
between it and Postgres. A rule's ``target_qbo_account_id`` is valid only while it is a
current categorization target (active, non-AR/AP — ``accounts.categorization_targets``);
otherwise the rule is kept but flagged ``invalid_target`` and skipped at match time.

Public seam for tickets 10/12 (import from ``app.features.categorization``)::

    run_categorization(conn, book_id) -> dict
        Re-validate rule targets, then apply rules to every non-manual transaction
        (manual overrides are preserved and survive the re-run). Returns a summary.
    set_manual_category(conn, book_id, transaction_id, qbo_account_id) -> dict | None
        Manually assign an account; source becomes 'manual' and beats rules.
    clear_category(conn, book_id, transaction_id) -> dict | None
        Reset a transaction to uncategorized (source 'none').
    revalidate_rules(conn, book_id) -> int
        Re-flag invalid_target for the Book's rules against the live mirror.
    valid_target_ids(conn, book_id) -> set[str]
        The qbo_account_ids categorization may target right now.
"""

from __future__ import annotations

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.features.accounts import categorization_targets
from app.features.categorization import engine
from app.features.csv_import import list_imported_transactions

_RULE_COLS = (
    "id, book_id, priority, target_qbo_account_id, conditions, invalid_target, "
    "created_at, updated_at"
)


# ----------------------------------------------------------------- targets ---


async def valid_target_ids(conn: AsyncConnection, book_id) -> set[str]:
    """The qbo_account_ids a rule/manual override may target right now (active,
    non-AR/AP). Reads the mirror via the accounts seam so the rule stays in one place."""
    grouped = await categorization_targets(conn, book_id)
    return {a["qbo_id"] for accounts in grouped.values() for a in accounts}


# ------------------------------------------------------------------- rules ---


async def list_rules(conn: AsyncConnection, book_id) -> list[dict]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"SELECT {_RULE_COLS} FROM categorization_rules "
            f"WHERE book_id = %s ORDER BY priority, created_at",
            (book_id,),
        )
        return await cur.fetchall()


async def create_rule(
    conn: AsyncConnection, book_id, priority: int, target_qbo_account_id: str, conditions: list
) -> dict:
    engine.validate_conditions(conditions)
    invalid = target_qbo_account_id not in await valid_target_ids(conn, book_id)
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"INSERT INTO categorization_rules "
            f"(book_id, priority, target_qbo_account_id, conditions, invalid_target) "
            f"VALUES (%s, %s, %s, %s, %s) RETURNING {_RULE_COLS}",
            (book_id, priority, target_qbo_account_id, Jsonb(conditions), invalid),
        )
        return await cur.fetchone()


async def update_rule(
    conn: AsyncConnection, book_id, rule_id, *, priority=None, target_qbo_account_id=None, conditions=None
) -> dict | None:
    if conditions is not None:
        engine.validate_conditions(conditions)
    sets, params = [], []
    if priority is not None:
        sets.append("priority = %s")
        params.append(priority)
    if conditions is not None:
        sets.append("conditions = %s")
        params.append(Jsonb(conditions))
    if target_qbo_account_id is not None:
        sets.append("target_qbo_account_id = %s")
        params.append(target_qbo_account_id)
        # Re-validate the flag whenever the target changes.
        sets.append("invalid_target = %s")
        params.append(target_qbo_account_id not in await valid_target_ids(conn, book_id))
    if not sets:
        return await _get_rule(conn, book_id, rule_id)
    sets.append("updated_at = now()")
    params += [rule_id, book_id]
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"UPDATE categorization_rules SET {', '.join(sets)} "
            f"WHERE id = %s AND book_id = %s RETURNING {_RULE_COLS}",
            params,
        )
        return await cur.fetchone()


async def delete_rule(conn: AsyncConnection, book_id, rule_id) -> bool:
    cur = await conn.execute(
        "DELETE FROM categorization_rules WHERE id = %s AND book_id = %s",
        (rule_id, book_id),
    )
    return cur.rowcount > 0


async def _get_rule(conn: AsyncConnection, book_id, rule_id) -> dict | None:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"SELECT {_RULE_COLS} FROM categorization_rules WHERE id = %s AND book_id = %s",
            (rule_id, book_id),
        )
        return await cur.fetchone()


async def revalidate_rules(conn: AsyncConnection, book_id) -> int:
    """Re-flag invalid_target against the live mirror. Only touches rows whose flag
    actually changes (so updated_at stays meaningful). Returns rows changed."""
    valid = list(await valid_target_ids(conn, book_id))
    # target = ANY(valid) -> currently valid; NOT(...) -> invalid_target.
    cur = await conn.execute(
        "UPDATE categorization_rules SET invalid_target = NOT (target_qbo_account_id = ANY(%s)), "
        "updated_at = now() "
        "WHERE book_id = %s "
        "  AND invalid_target IS DISTINCT FROM NOT (target_qbo_account_id = ANY(%s))",
        (valid, book_id, valid),
    )
    return cur.rowcount


# --------------------------------------------------------------- categorize ---


async def run_categorization(conn: AsyncConnection, book_id) -> dict:
    """Re-validate targets, then apply rules to every non-manual transaction. A
    ``category_source='manual'`` row is left untouched — manual beats rules and
    survives the re-run. Returns ``{total, by_rule, manual, uncategorized}``."""
    await revalidate_rules(conn, book_id)
    rules = await list_rules(conn, book_id)  # matcher skips invalid_target rows itself
    txns = await list_imported_transactions(conn, book_id)

    manual = [t for t in txns if t["category_source"] == "manual"]
    auto = [t for t in txns if t["category_source"] != "manual"]

    params, by_rule = [], 0
    for t in auto:
        r = engine.categorize_one(t, rules)
        if r["source"] == "rule":
            by_rule += 1
        params.append((r["account_id"], r["source"], r["rule_id"], t["id"]))

    if params:
        async with conn.cursor() as cur:
            await cur.executemany(
                "UPDATE imported_transactions "
                "SET assigned_account_qbo_id = %s, category_source = %s, matched_rule_id = %s "
                "WHERE id = %s",
                params,
            )

    return {
        "total": len(txns),
        "by_rule": by_rule,
        "manual": len(manual),
        "uncategorized": len(auto) - by_rule,
    }


async def set_manual_category(
    conn: AsyncConnection, book_id, transaction_id, qbo_account_id: str
) -> dict | None:
    """Manually assign an account: source -> 'manual', matched_rule_id cleared. Manual
    beats rules and survives a re-run. Returns the updated row, or None if the
    transaction isn't in this Book. Target validity is checked by the caller."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "UPDATE imported_transactions "
            "SET assigned_account_qbo_id = %s, category_source = 'manual', matched_rule_id = NULL "
            "WHERE id = %s AND book_id = %s "
            "RETURNING id, assigned_account_qbo_id, category_source, matched_rule_id",
            (qbo_account_id, transaction_id, book_id),
        )
        return await cur.fetchone()


async def clear_category(conn: AsyncConnection, book_id, transaction_id) -> dict | None:
    """Reset a transaction to uncategorized (source 'none'). Returns the updated row,
    or None if the transaction isn't in this Book."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "UPDATE imported_transactions "
            "SET assigned_account_qbo_id = NULL, category_source = 'none', matched_rule_id = NULL "
            "WHERE id = %s AND book_id = %s "
            "RETURNING id, assigned_account_qbo_id, category_source, matched_rule_id",
            (transaction_id, book_id),
        )
        return await cur.fetchone()
