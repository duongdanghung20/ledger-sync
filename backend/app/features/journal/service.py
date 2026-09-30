"""Journal DB layer: materialize (approve), discard (un-approve), and read.

The pure sign rule lives in ``engine.py``; this module only moves data between it
and Postgres. Approval snapshots the category + cash Account, the abs amount, the
debit side and a memo onto one ``journal_entries`` row, so the entry is immutable to
later rule re-runs or re-categorization (nothing here reads the transaction's
category again once the entry exists).

A transaction is *approved* iff a Journal Entry row exists for it (1:1); there is no
extra column on the transaction. Un-approving a still-``pending`` entry deletes that
row, which returns the transaction to plain "categorized".

Public seam (import from ``app.features.journal``)::

    approve(conn, book_id, transaction_id) -> journal_entry_row (dict)
    unapprove(conn, book_id, journal_entry_id) -> None
    preview_transaction(conn, book_id, transaction_id) -> (debit, credit)
    list_journal_entries(conn, book_id) -> list[dict]        # ticket 12 review surface
    get_journal_entry(conn, book_id, journal_entry_id) -> dict | None  # ticket 11 fetch

The pure ``preview(txn_row) -> lines`` (row must carry ``cash_account_qbo_id``) lives
in ``engine`` and is the reusable primitive; ``preview_transaction`` is its DB-resolving
wrapper.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from psycopg import AsyncConnection
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row

from app.features.bank_accounts import get_bank_account
from app.features.csv_import import list_imported_transactions
from app.features.journal import engine

_JE_COLS = (
    "id, book_id, imported_transaction_id, category_account_qbo_id, cash_account_qbo_id, "
    "amount, debit_side, memo, sync_status, qbo_id, requestid, doc_number, posted_at, "
    "last_error, last_error_code, last_attempt_at, attempt_count, created_at"
)


def _memo(txn: dict) -> str:
    """Carry description (+ payee) into the memo → QBO Description / PrivateNote."""
    desc = (txn.get("description") or "").strip()
    payee = (txn.get("payee") or "").strip()
    return f"{desc} — {payee}" if payee else desc


async def _get_txn(conn: AsyncConnection, book_id, transaction_id) -> dict | None:
    """The one canonical transaction row, scoped to the Book. Reuses the csv_import
    seam so the row shape stays defined in one place."""
    # ponytail: O(n) scan reusing the list seam, fine for interactive single approvals;
    # add a get_imported_transaction(conn, book_id, id) seam to csv_import if it's hot.
    for t in await list_imported_transactions(conn, book_id):
        if str(t["id"]) == str(transaction_id):
            return t
    return None


async def resolve_cash_qbo_id(conn: AsyncConnection, book_id, bank_account_id) -> str:
    """The cash-side Account for a Bank Account, or raise ``UnmappedBankAccount``.

    A Bank Account is *unmapped for journal purposes* when its ``qbo_account_id`` no
    longer resolves to an **active** Account in the ticket-06 mirror — block and flag,
    never post to a stale/inactive account."""
    bank = await get_bank_account(conn, bank_account_id)
    if bank is None or not bank["qbo_account_id"]:
        raise engine.UnmappedBankAccount("This transaction's bank account is not mapped.")
    cur = await conn.execute(
        "SELECT active FROM accounts WHERE book_id = %s AND qbo_id = %s",
        (book_id, bank["qbo_account_id"]),
    )
    row = await cur.fetchone()
    if row is None or not row[0]:
        raise engine.UnmappedBankAccount(
            "This transaction's bank account maps to an account that is no longer "
            "active in QuickBooks — re-map it before approving."
        )
    return bank["qbo_account_id"]


async def preview_transaction(conn: AsyncConnection, book_id, transaction_id) -> tuple:
    """The two lines a categorized transaction would post, without materializing.
    Resolves the cash side (raising ``UnmappedBankAccount``) then defers to the pure
    ``engine.preview``. Raises ``NotFound`` / ``Uncategorized`` / ``ZeroAmount`` too."""
    txn = await _get_txn(conn, book_id, transaction_id)
    if txn is None:
        raise engine.NotFound("Transaction not found.")
    if not txn["assigned_account_qbo_id"]:
        raise engine.Uncategorized("Assign an account before this transaction can be journaled.")
    txn = {**txn, "cash_account_qbo_id": await resolve_cash_qbo_id(conn, book_id, txn["bank_account_id"])}
    return engine.preview(txn)


async def approve(conn: AsyncConnection, book_id, transaction_id) -> dict:
    """Materialize exactly one balanced Journal Entry for a categorized transaction.

    Rejects (nothing persisted) an uncategorized transaction, a zero amount, or an
    unmapped Bank Account. Mints a ``requestid`` (never regenerated) and a per-Book
    monotonic ``doc_number``. Re-approving raises ``AlreadyApproved`` (1:1)."""
    txn = await _get_txn(conn, book_id, transaction_id)
    if txn is None:
        raise engine.NotFound("Transaction not found.")

    category = txn["assigned_account_qbo_id"]
    if not category:
        raise engine.Uncategorized("Assign an account before approving this transaction.")

    cash = await resolve_cash_qbo_id(conn, book_id, txn["bank_account_id"])
    debit, _credit = engine.build_lines(txn["amount"], category, cash)  # raises ZeroAmount
    side = engine.debit_side(debit, category)
    abs_amount = debit.amount

    # Reject re-approval before consuming a doc_number (unique index is the race backstop).
    cur = await conn.execute(
        "SELECT 1 FROM journal_entries WHERE imported_transaction_id = %s", (txn["id"],)
    )
    if await cur.fetchone() is not None:
        raise engine.AlreadyApproved("This transaction already has a journal entry.")

    doc_number = await _next_doc_number(conn, book_id)
    requestid = uuid4()
    try:
        async with conn.cursor(row_factory=dict_row) as c:
            await c.execute(
                f"INSERT INTO journal_entries "
                f"(book_id, imported_transaction_id, category_account_qbo_id, "
                f" cash_account_qbo_id, amount, debit_side, memo, requestid, doc_number) "
                f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING {_JE_COLS}",
                (book_id, txn["id"], category, cash, abs_amount, side, _memo(txn),
                 requestid, doc_number),
            )
            return await c.fetchone()
    except UniqueViolation:  # concurrent double-approve
        raise engine.AlreadyApproved("This transaction already has a journal entry.")


async def _next_doc_number(conn: AsyncConnection, book_id) -> int:
    """Atomically bump and return the Book's monotonic doc-number counter. The
    ON CONFLICT DO UPDATE row-locks the counter, so concurrent approvals serialize."""
    cur = await conn.execute(
        "INSERT INTO journal_doc_counters (book_id, value) VALUES (%s, 1) "
        "ON CONFLICT (book_id) DO UPDATE SET value = journal_doc_counters.value + 1 "
        "RETURNING value",
        (book_id,),
    )
    return (await cur.fetchone())[0]


async def unapprove(conn: AsyncConnection, book_id, journal_entry_id) -> None:
    """Discard a still-``pending`` entry (delete the row → back to categorized).

    Un-approve is allowed ONLY for ``sync_status = 'pending'`` (the WHERE below is the
    guard). Any other state refuses with ``NotPending``: an ``attempting`` entry may
    already be in QuickBooks (its POST reached QBO before a crash), so discarding it
    would strand the JE and let a re-approve mint a fresh, double-posting
    ``requestid``; a ``posted``/``failed`` entry likewise keeps its ``requestid``. A
    missing entry raises ``NotFound``."""
    cur = await conn.execute(
        "DELETE FROM journal_entries WHERE id = %s AND book_id = %s AND sync_status = 'pending' "
        "RETURNING id",
        (journal_entry_id, book_id),
    )
    if await cur.fetchone() is not None:
        return
    # Nothing deleted: distinguish "not pending" from "not found" for a clear error.
    cur = await conn.execute(
        "SELECT sync_status FROM journal_entries WHERE id = %s AND book_id = %s",
        (journal_entry_id, book_id),
    )
    row = await cur.fetchone()
    if row is None:
        raise engine.NotFound("Journal entry not found.")
    raise engine.NotPending(f"A {row[0]} entry cannot be un-approved.")


async def list_journal_entries(conn: AsyncConnection, book_id) -> list[dict]:
    """Every Journal Entry in the Book, newest first — ticket 12's review surface."""
    async with conn.cursor(row_factory=dict_row) as c:
        await c.execute(
            f"SELECT {_JE_COLS} FROM journal_entries WHERE book_id = %s "
            f"ORDER BY created_at DESC, doc_number DESC",
            (book_id,),
        )
        return await c.fetchall()


async def get_journal_entry(conn: AsyncConnection, book_id, journal_entry_id) -> dict | None:
    """One Journal Entry row scoped to the Book, or None — ticket 11 fetches this to
    map through ``engine.to_port_entry`` before pushing."""
    async with conn.cursor(row_factory=dict_row) as c:
        await c.execute(
            f"SELECT {_JE_COLS} FROM journal_entries WHERE id = %s AND book_id = %s",
            (journal_entry_id, book_id),
        )
        return await c.fetchone()
