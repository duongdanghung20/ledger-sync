"""CSV import DB layer: Column-Mapping Profile CRUD, header-signature suggestion,
and the import that maps -> dedups (per Bank Account) -> inserts.

The parse/dedup logic is the pure engine (``engine.py``); this module only moves
data between it and Postgres. Every import runs inside the request's single
transaction (the pooled connection commits at request end), so a mid-insert
failure imports nothing rather than half a file.

Public seam for tickets 09/10 (import from ``app.features.csv_import``)::

    list_imported_transactions(conn, book_id, *, bank_account_id=None) -> list[dict]
        Canonical imported rows incl. bank_account_id, signed amount (Decimal),
        date, description, payee, and the categorization columns 09 fills.
"""

from __future__ import annotations

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.features.csv_import import engine

# Distinguishes "leave bank_account_id alone" from "clear it" in a PATCH.
_UNSET = object()

_PROFILE_COLS = "id, book_id, name, bank_account_id, config, created_at"

# The canonical + categorization columns tickets 09/10 read.
_TX_COLS = (
    "id, book_id, bank_account_id, column_mapping_profile_id, date, amount, "
    "description, payee, external_id, imported_at, dedup_key, "
    "assigned_account_qbo_id, category_source, matched_rule_id"
)


# --------------------------------------------------------------- profiles ----


async def list_profiles(conn: AsyncConnection, book_id) -> list[dict]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"SELECT {_PROFILE_COLS} FROM column_mapping_profiles "
            f"WHERE book_id = %s ORDER BY created_at, name",
            (book_id,),
        )
        return await cur.fetchall()


async def create_profile(
    conn: AsyncConnection, book_id, name: str, config: dict, bank_account_id
) -> dict:
    """Persist a profile. ``config`` is validated against the engine's contract
    (raises ConfigError -> 422 upstream) so a broken mapping can never import."""
    engine.validate_config(config)
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"INSERT INTO column_mapping_profiles (book_id, name, bank_account_id, config) "
            f"VALUES (%s, %s, %s, %s) RETURNING {_PROFILE_COLS}",
            (book_id, name, bank_account_id, Jsonb(config)),
        )
        return await cur.fetchone()


async def update_profile(
    conn: AsyncConnection, book_id, profile_id, *, name=None, config=None, bank_account_id=_UNSET
) -> dict | None:
    if config is not None:
        engine.validate_config(config)
    sets, params = [], []
    if name is not None:
        sets.append("name = %s")
        params.append(name)
    if config is not None:
        sets.append("config = %s")
        params.append(Jsonb(config))
    if bank_account_id is not _UNSET:
        sets.append("bank_account_id = %s")
        params.append(bank_account_id)
    if not sets:
        return await get_profile(conn, book_id, profile_id)
    params += [profile_id, book_id]
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"UPDATE column_mapping_profiles SET {', '.join(sets)} "
            f"WHERE id = %s AND book_id = %s RETURNING {_PROFILE_COLS}",
            params,
        )
        return await cur.fetchone()


async def get_profile(conn: AsyncConnection, book_id, profile_id) -> dict | None:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"SELECT {_PROFILE_COLS} FROM column_mapping_profiles "
            f"WHERE id = %s AND book_id = %s",
            (profile_id, book_id),
        )
        return await cur.fetchone()


async def delete_profile(conn: AsyncConnection, book_id, profile_id) -> bool:
    cur = await conn.execute(
        "DELETE FROM column_mapping_profiles WHERE id = %s AND book_id = %s",
        (profile_id, book_id),
    )
    return cur.rowcount > 0


def _referenced_columns(config: dict) -> set[str]:
    """The column-name set a profile maps — its header signature."""
    cols = {
        engine._col(config, "date"),
        engine._col(config, "description"),
        engine._col(config, "payee"),
        engine._col(config, "external_id"),
    }
    amount = config.get("amount") or {}
    cols |= {amount.get("column"), amount.get("debit_column"), amount.get("credit_column")}
    return {c for c in cols if c}


def suggest_profile(profiles: list[dict], header: list[str]) -> dict | None:
    """Pick the saved profile whose mapped columns are all present in ``header``,
    preferring the most specific (most columns matched). None if none fits."""
    present = {h.strip() for h in header}
    matches = [
        (len(cols), p)
        for p in profiles
        if (cols := _referenced_columns(p["config"])) and cols <= present
    ]
    if not matches:
        return None
    matches.sort(key=lambda m: m[0], reverse=True)  # profiles already come created_at-ordered
    return matches[0][1]


# ----------------------------------------------------------------- import ----


async def _existing_keys(conn: AsyncConnection, bank_account_id) -> list[str]:
    cur = await conn.execute(
        "SELECT dedup_key FROM imported_transactions WHERE bank_account_id = %s",
        (bank_account_id,),
    )
    return [row[0] for row in await cur.fetchall()]


async def import_csv(
    conn: AsyncConnection, book_id, bank_account_id, profile: dict, text: str
) -> dict:
    """Map -> dedup (per Bank Account) -> insert. Returns a review summary:
    imported rows, rejected rows (with reasons), the already-imported duplicate
    count, and the possible in-file duplicate count."""
    mapped = engine.map_rows(text, profile["config"])
    existing = await _existing_keys(conn, bank_account_id)
    result = engine.reconcile(mapped.good, existing)

    if result.to_import:
        params = [
            (
                book_id,
                bank_account_id,
                profile["id"],
                row.date,
                row.amount,
                row.description,
                row.payee or None,
                row.external_id or None,
                Jsonb(row.raw),
                engine.dedup_key(row),
            )
            for row in result.to_import
        ]
        async with conn.cursor() as cur:
            await cur.executemany(
                "INSERT INTO imported_transactions "
                "(book_id, bank_account_id, column_mapping_profile_id, date, amount, "
                " description, payee, external_id, raw, dedup_key) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                params,
            )

    return {
        "imported": len(result.to_import),
        "duplicates": len(result.duplicates),
        "in_file_duplicate_count": result.in_file_duplicate_count,
        "rejected": [{"raw": b.raw, "reasons": b.reasons} for b in mapped.bad],
    }


# ---------------------------------------------------- seam (tickets 09/10) ---


async def list_imported_transactions(
    conn: AsyncConnection, book_id, *, bank_account_id=None
) -> list[dict]:
    """Canonical imported rows for a Book (optionally one Bank Account), each incl.
    bank_account_id, signed ``amount`` (Decimal), date, description, payee, and the
    categorization columns ticket 09 fills. Newest first."""
    sql = f"SELECT {_TX_COLS} FROM imported_transactions WHERE book_id = %s"
    params: list = [book_id]
    if bank_account_id is not None:
        sql += " AND bank_account_id = %s"
        params.append(bank_account_id)
    sql += " ORDER BY date DESC, imported_at DESC"
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(sql, params)
        return await cur.fetchall()
