"""Journal HTTP surface — Bookkeeper-allowed (guarded by ``current_user``).

The backend for ticket 12's review surface: preview a categorized transaction's two
debit/credit lines, approve it (materialize one balanced Journal Entry), un-approve a
still-pending entry, and list the Book's entries. The frontend review route itself is
ticket 12; the debit/credit push mapping is ticket 11.

- GET  /api/journal/entries                              list the Book's Journal Entries
- GET  /api/journal/transactions/{id}/preview           the two lines, without materializing
- POST /api/journal/transactions/{id}/approve           materialize one balanced entry
- POST /api/journal/entries/{id}/unapprove              discard a still-pending entry
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from psycopg import AsyncConnection

from app.db import get_conn
from app.features.auth.deps import current_user
from app.features.journal import engine, service
from app.features.qbo import JournalLine
from app.features.qbo_connection.service import book_id_for_org

router = APIRouter(prefix="/api/journal", tags=["journal"])


def _line_json(line: JournalLine) -> dict:
    # Amount as a string: 2dp Decimal preserved, never coerced through float.
    return {"account_id": line.account_id, "posting_type": line.posting_type,
            "amount": str(line.amount)}


def _lines_json(lines: tuple[JournalLine, JournalLine]) -> dict:
    debit, credit = lines
    return {
        "lines": [_line_json(debit), _line_json(credit)],
        "debit_total": str(debit.amount),
        "credit_total": str(credit.amount),
        "balanced": debit.amount == credit.amount,
    }


def _je_json(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "imported_transaction_id": str(row["imported_transaction_id"]),
        "category_account_qbo_id": row["category_account_qbo_id"],
        "cash_account_qbo_id": row["cash_account_qbo_id"],
        "amount": str(row["amount"]),
        "debit_side": row["debit_side"],
        "memo": row["memo"],
        "sync_status": row["sync_status"],
        "qbo_id": row["qbo_id"],
        "requestid": str(row["requestid"]),
        "doc_number": row["doc_number"],
        "lines": _lines_json(engine.build_lines(
            -row["amount"] if row["debit_side"] == "category" else row["amount"],
            row["category_account_qbo_id"], row["cash_account_qbo_id"],
        ))["lines"],
    }


@router.get("/entries")
async def list_entries(
    user: dict = Depends(current_user), conn: AsyncConnection = Depends(get_conn)
) -> dict:
    book_id = await book_id_for_org(conn, user["organization_id"])
    return {"entries": [_je_json(r) for r in await service.list_journal_entries(conn, book_id)]}


@router.get("/transactions/{transaction_id}/preview")
async def preview(
    transaction_id: UUID,
    user: dict = Depends(current_user),
    conn: AsyncConnection = Depends(get_conn),
) -> dict:
    book_id = await book_id_for_org(conn, user["organization_id"])
    try:
        return _lines_json(await service.preview_transaction(conn, book_id, transaction_id))
    except engine.JournalError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))


@router.post("/transactions/{transaction_id}/approve", status_code=201)
async def approve(
    transaction_id: UUID,
    user: dict = Depends(current_user),
    conn: AsyncConnection = Depends(get_conn),
) -> dict:
    book_id = await book_id_for_org(conn, user["organization_id"])
    try:
        return _je_json(await service.approve(conn, book_id, transaction_id))
    except engine.JournalError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))


@router.post("/entries/{journal_entry_id}/unapprove")
async def unapprove(
    journal_entry_id: UUID,
    user: dict = Depends(current_user),
    conn: AsyncConnection = Depends(get_conn),
) -> dict:
    book_id = await book_id_for_org(conn, user["organization_id"])
    try:
        await service.unapprove(conn, book_id, journal_entry_id)
    except engine.JournalError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    return {"ok": True}
