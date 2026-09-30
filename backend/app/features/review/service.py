"""Review read model: one row per imported transaction with DERIVED state.

State is never stored — it's computed from two facts that already live elsewhere:
the transaction's category assignment (csv_import) and, if one exists, its Journal
Entry's sync_status (journal). So there is no status column to drift out of sync
with the truth. This module only joins the existing seams in memory; no new SQL,
no new table.

    uncategorized  no assigned_account_qbo_id, no Journal Entry
    categorized    assigned, no Journal Entry
    approved       a Journal Entry exists with sync_status = 'pending' or 'attempting'
    posted         its sync_status = 'posted'
    failed         its sync_status = 'failed'

An 'attempting' entry is a pending entry mid-push (the money-path marker committed
before its QBO POST); the reviewer sees it exactly as 'approved'.
"""

from __future__ import annotations

from psycopg import AsyncConnection

from app.features.csv_import import list_imported_transactions
from app.features.journal import list_journal_entries

# pending/attempting are "approved" to the reviewer; posted/failed pass through as-is.
_STATE_FROM_SYNC = {
    "pending": "approved",
    "attempting": "approved",
    "posted": "posted",
    "failed": "failed",
}


def _state(txn: dict, je: dict | None) -> str:
    if je is None:
        return "categorized" if txn["assigned_account_qbo_id"] else "uncategorized"
    return _STATE_FROM_SYNC[je["sync_status"]]


def _row(txn: dict, je: dict | None) -> dict:
    row = {
        "id": str(txn["id"]),
        "date": txn["date"].isoformat(),
        "amount": str(txn["amount"]),  # 2dp Decimal as string, never through float
        "description": txn["description"],
        "payee": txn["payee"],
        "bank_account_id": str(txn["bank_account_id"]),
        "assigned_account_qbo_id": txn["assigned_account_qbo_id"],
        "category_source": txn["category_source"],
        "state": _state(txn, je),
        "journal_entry": None,
    }
    if je is not None:
        row["journal_entry"] = {
            "id": str(je["id"]),
            "sync_status": je["sync_status"],
            "doc_number": je["doc_number"],
            "last_error": je["last_error"],
            "last_error_code": je["last_error_code"],
            "attempt_count": je["attempt_count"],
        }
    return row


async def review_rows(conn: AsyncConnection, book_id) -> list[dict]:
    """Every imported transaction in the Book (newest first, from the csv_import
    seam) joined to its Journal Entry, with derived state. The join is in memory
    over the two existing list seams — a Book's transaction set is bounded and read
    interactively, so this is the smallest thing that works and reuses the row
    shapes defined in one place."""
    entries = {
        str(e["imported_transaction_id"]): e
        for e in await list_journal_entries(conn, book_id)
    }
    txns = await list_imported_transactions(conn, book_id)
    return [_row(t, entries.get(str(t["id"]))) for t in txns]
