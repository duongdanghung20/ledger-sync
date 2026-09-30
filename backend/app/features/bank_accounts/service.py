"""Bank Accounts: create + one-to-one mapping to a cash-side Account, and the
read seam tickets 08/10 consume.

The mapping target is validated against the Chart-of-Accounts mirror (ticket 06):
the chosen ``qbo_account_id`` must be an Account that exists AND is active in the
Book's mirror at create/remap time — a missing or inactive target is rejected
(422). ``qbo_account_id`` is then stored as a plain value; there is no cross-table
FK to ``accounts`` (see the migration + downstream note), so a later QuickBooks
deactivation is never blocked and simply renders the Bank Account "unmapped" to
tickets 10/11.
"""

from __future__ import annotations

from fastapi import HTTPException
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from app.features.accounts import list_accounts

# id, book_id, name, qbo_account_id, created_at — the row shape the seam returns.
_COLS = "id, book_id, name, qbo_account_id, created_at"

_INVALID_TARGET = HTTPException(
    status_code=422,
    detail="Map this bank account to an active account from the chart of accounts.",
)


async def _active_qbo_ids(conn: AsyncConnection, book_id) -> set[str]:
    """The ``qbo_id`` of every *active* Account in the Book's mirror. Reuses the
    ticket-06 mirror lookup (``list_accounts``) rather than re-querying here, so
    "active" stays defined in one place."""
    return {a["qbo_id"] for a in await list_accounts(conn, book_id) if a["active"]}


async def _require_active_target(conn: AsyncConnection, book_id, qbo_account_id: str) -> None:
    if qbo_account_id not in await _active_qbo_ids(conn, book_id):
        raise _INVALID_TARGET


async def create_bank_account(
    conn: AsyncConnection, book_id, name: str, qbo_account_id: str
) -> dict:
    """Create a Bank Account mapped to ``qbo_account_id``. Rejects (422) a target
    that isn't an active Account in the Book's mirror."""
    await _require_active_target(conn, book_id, qbo_account_id)
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"INSERT INTO bank_accounts (book_id, name, qbo_account_id) "
            f"VALUES (%s, %s, %s) RETURNING {_COLS}",
            (book_id, name, qbo_account_id),
        )
        return await cur.fetchone()


async def remap_bank_account(
    conn: AsyncConnection,
    book_id,
    bank_account_id,
    *,
    name: str | None = None,
    qbo_account_id: str | None = None,
) -> dict | None:
    """Re-map (and/or rename) a Bank Account in this Book. A new mapping target is
    validated against the active mirror (422 if invalid). Returns the updated row,
    or None if no Bank Account with that id exists in the Book."""
    if qbo_account_id is not None:
        await _require_active_target(conn, book_id, qbo_account_id)
    sets, params = [], []
    if name is not None:
        sets.append("name = %s")
        params.append(name)
    if qbo_account_id is not None:
        sets.append("qbo_account_id = %s")
        params.append(qbo_account_id)
    params += [bank_account_id, book_id]
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"UPDATE bank_accounts SET {', '.join(sets)} "
            f"WHERE id = %s AND book_id = %s RETURNING {_COLS}",
            params,
        )
        return await cur.fetchone()


async def list_bank_accounts(conn: AsyncConnection, book_id) -> list[dict]:
    """Every Bank Account in the Book, each row incl. ``qbo_account_id``. The seam
    ticket 08 (import) reads."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"SELECT {_COLS} FROM bank_accounts WHERE book_id = %s "
            f"ORDER BY created_at, name",
            (book_id,),
        )
        return await cur.fetchall()


async def get_bank_account(conn: AsyncConnection, bank_account_id) -> dict | None:
    """One Bank Account row (incl. ``qbo_account_id``), or None. The seam ticket 10
    (journal) reads to resolve the mapped cash-side Account."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"SELECT {_COLS} FROM bank_accounts WHERE id = %s", (bank_account_id,)
        )
        return await cur.fetchone()
