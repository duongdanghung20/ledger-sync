"""Chart-of-Accounts mirror: reconcile, refresh, and categorization targets.

QBO is the source of truth for the Chart of Accounts; Ledger-Sync only reads it
down and never writes an Account back. Everything here goes through the qbo port
(``client.query_accounts``) and the connection seam (``get_valid_connection``) —
no direct httpx, no token handling.

Public seam for later tickets (import from ``app.features.accounts``)::

    reconcile(conn, book_id, qbo_accounts) -> dict
        Upsert by (book_id, qbo_id): rename/retype update in place; an account
        absent from ``qbo_accounts`` is flipped active=false (never deleted); a
        returning account is not duplicated. Pure DB step over data you supply.

    refresh(conn, book_id, client) -> QboResult
        Fetch the full CoA (active + inactive) via the port, reconcile it, and
        stamp books.last_synced_at. Used on connect, by the manual endpoint, and
        under refresh_if_stale. Returns the port QboResult (mirror untouched on a
        non-OK fetch); raises 409 if the Book isn't connected.

    refresh_if_stale(conn, book_id, client, *, max_age=15m) -> bool
        The lazy pre-read guard tickets 09/12 (review) and push call before they
        read the mirror. Refreshes when never-synced or older than max_age;
        returns whether it refreshed.

    categorization_targets(conn, book_id) -> dict[str, list[dict]]
        Active accounts only, grouped by Classification, EXCLUDING
        Accounts-Receivable / Accounts-Payable types (QBO rejects a JE line to
        those without a customer/vendor ref). Consumed by 09/12.

    last_synced_at(conn, book_id) -> datetime | None
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from psycopg import AsyncConnection
from psycopg.rows import dict_row

from app.features.qbo import QboClient, QboResult
from app.features.qbo_connection import get_valid_connection

# AccountType values that categorization must never offer: a JE line to an
# Accounts-Receivable / Accounts-Payable account is rejected by QBO unless it
# also carries a customer/vendor ref, which Ledger-Sync's rules engine doesn't
# supply. (Canonical QBO AccountType JSON strings — Intuit Account entity.)
_NON_TARGET_TYPES = ("Accounts Receivable", "Accounts Payable")

# How long a mirror may go unrefreshed before a lazy read re-syncs it.
_DEFAULT_MAX_AGE = timedelta(minutes=15)

_UPSERT = """
    INSERT INTO accounts
        (book_id, qbo_id, name, account_type, account_sub_type,
         classification, acct_num, active, sync_token, parent_qbo_id)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (book_id, qbo_id) DO UPDATE SET
        name             = EXCLUDED.name,
        account_type     = EXCLUDED.account_type,
        account_sub_type = EXCLUDED.account_sub_type,
        classification   = EXCLUDED.classification,
        acct_num         = EXCLUDED.acct_num,
        active           = EXCLUDED.active,
        sync_token       = EXCLUDED.sync_token,
        parent_qbo_id    = EXCLUDED.parent_qbo_id
"""


def _row_params(book_id, acct: dict) -> tuple:
    """Map one QBO Account dict to the upsert's positional params. Defensive
    about a missing/absent field — the shape comes from an external API."""
    parent = acct.get("ParentRef")
    parent_qbo_id = parent.get("value") if isinstance(parent, dict) else None
    sync_token = acct.get("SyncToken")
    return (
        book_id,
        str(acct["Id"]),
        acct.get("Name") or acct.get("FullyQualifiedName") or "",
        acct.get("AccountType"),
        acct.get("AccountSubType"),
        acct.get("Classification"),
        acct.get("AcctNum"),
        bool(acct.get("Active", True)),
        str(sync_token) if sync_token is not None else None,
        parent_qbo_id,
    )


async def reconcile(conn: AsyncConnection, book_id, qbo_accounts: list[dict]) -> dict:
    """Reconcile the mirror to ``qbo_accounts`` (the full CoA the port returned).

    Upsert every account by (book_id, qbo_id), then flip any *currently-active*
    row whose qbo_id is absent from the result to ``active = false`` — a soft
    deactivate, never a delete, so historical references still resolve and a
    reactivated account is the same row, not a duplicate.
    """
    present = [str(a["Id"]) for a in qbo_accounts]

    async with conn.cursor() as cur:
        if qbo_accounts:
            await cur.executemany(_UPSERT, [_row_params(book_id, a) for a in qbo_accounts])
        # qbo_id <> ALL('{}') is true for every row, so an empty result correctly
        # deactivates the whole mirror (QBO returned nothing) — but refresh only
        # reconciles a successful fetch, never an errored one.
        await cur.execute(
            "UPDATE accounts SET active = false "
            "WHERE book_id = %s AND active = true AND qbo_id <> ALL(%s)",
            (book_id, present),
        )
        deactivated = cur.rowcount
    return {"upserted": len(qbo_accounts), "deactivated": deactivated}


async def refresh(conn: AsyncConnection, book_id, client: QboClient) -> QboResult:
    """Fetch the full CoA via the port and reconcile it, stamping
    ``books.last_synced_at`` on success. Mirror is left untouched on a non-OK
    fetch. Raises 409 (from ``get_valid_connection``) if the Book isn't
    connected."""
    connection = await get_valid_connection(conn, book_id, client)
    result = await client.query_accounts(connection)
    if not result.ok:
        return result
    await reconcile(conn, book_id, result.data or [])
    await conn.execute(
        "UPDATE books SET last_synced_at = now() WHERE id = %s", (book_id,)
    )
    return result


async def refresh_if_stale(
    conn: AsyncConnection, book_id, client: QboClient, *, max_age: timedelta = _DEFAULT_MAX_AGE
) -> bool:
    """Refresh only when the mirror is stale (never synced, or older than
    ``max_age``). Returns whether a refresh ran. The pre-read guard for review /
    push so they never read a stale (or empty) mirror."""
    last = await last_synced_at(conn, book_id)
    if last is not None and last > datetime.now(timezone.utc) - max_age:
        return False
    result = await refresh(conn, book_id, client)
    return result.ok


async def last_synced_at(conn: AsyncConnection, book_id) -> datetime | None:
    cur = await conn.execute("SELECT last_synced_at FROM books WHERE id = %s", (book_id,))
    row = await cur.fetchone()
    return row[0] if row else None


async def list_accounts(conn: AsyncConnection, book_id) -> list[dict]:
    """The full mirror, inactive included, for a read-only view."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT qbo_id, name, account_type, account_sub_type, classification, "
            "acct_num, active, parent_qbo_id FROM accounts "
            "WHERE book_id = %s ORDER BY active DESC, classification, name",
            (book_id,),
        )
        return await cur.fetchall()


async def categorization_targets(conn: AsyncConnection, book_id) -> dict[str, list[dict]]:
    """Active accounts a Bookkeeper may categorize against, grouped by
    Classification and excluding AR/AP types. Importable by 09/12 so selection
    lives in one place, not duplicated per consumer."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT qbo_id, name, account_type, account_sub_type, classification, acct_num "
            "FROM accounts "
            "WHERE book_id = %s AND active = true "
            # NULL account_type is kept (never a real AR/AP), so a missing type
            # doesn't silently drop the account.
            "  AND (account_type IS NULL OR account_type <> ALL(%s)) "
            "ORDER BY classification, name",
            (book_id, list(_NON_TARGET_TYPES)),
        )
        rows = await cur.fetchall()

    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["classification"] or "Uncategorized", []).append(row)
    return grouped
