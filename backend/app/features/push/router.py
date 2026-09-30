"""Push HTTP surface — Bookkeeper-allowed (guarded by ``current_user``).

The backend for ticket 12's Sync-Status view + retry button. Pushing approved
Journal Entries to QuickBooks is idempotent by construction (see ``service``);
these routes just expose it. Sync status itself is read via ticket 10's
``GET /api/journal/entries``.

- POST /api/push                          push every pending entry in the Book
- POST /api/push/entries/{id}/retry       re-push one failed/pending entry

The qbo client is injected via ``get_qbo_client`` so tests swap in FakeQboClient
(same pattern as tickets 05/06). ``push_book`` / ``retry_entry`` run inside the
request's transaction, so the advisory lock and every status update commit
together when the handler returns.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from psycopg import AsyncConnection

from app.db import get_conn
from app.features.auth.deps import current_user
from app.features.push import service
from app.features.qbo import HttpxQboClient, QboClient
from app.features.qbo_connection.service import book_id_for_org

router = APIRouter(prefix="/api/push", tags=["push"])


def get_qbo_client() -> QboClient:
    """The QBO port. Overridden with FakeQboClient in tests."""
    return HttpxQboClient()


def _sync_json(row: dict) -> dict:
    """The sync fields ticket 12 shows for one entry."""
    return {
        "id": str(row["id"]),
        "sync_status": row["sync_status"],
        "qbo_id": row["qbo_id"],
        "last_error": row["last_error"],
        "last_error_code": row["last_error_code"],
        "last_attempt_at": row["last_attempt_at"].isoformat() if row["last_attempt_at"] else None,
        "attempt_count": row["attempt_count"],
    }


@router.post("")
async def push(
    user: dict = Depends(current_user),
    conn: AsyncConnection = Depends(get_conn),
    client: QboClient = Depends(get_qbo_client),
) -> dict:
    book_id = await book_id_for_org(conn, user["organization_id"])
    return await service.push_book(conn, book_id, client)  # 409 if not connected


@router.post("/entries/{journal_entry_id}/retry")
async def retry(
    journal_entry_id: UUID,
    user: dict = Depends(current_user),
    conn: AsyncConnection = Depends(get_conn),
    client: QboClient = Depends(get_qbo_client),
) -> dict:
    book_id = await book_id_for_org(conn, user["organization_id"])
    row = await service.retry_entry(conn, book_id, journal_entry_id, client)
    return _sync_json(row)
