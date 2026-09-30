"""Chart-of-Accounts HTTP surface.

Reading the mirror and refreshing it is a Bookkeeper-allowed operation ("As a
User"), so every route is guarded by ``current_user`` (any authenticated user),
not an Admin-only role.

- GET  /api/accounts          the full mirror (active + inactive) + last_synced_at
- POST /api/accounts/refresh  reconcile now (the manual + on-connect trigger)
- GET  /api/accounts/targets  active accounts grouped by Classification, no AR/AP

The qbo client is injected via ``get_qbo_client`` so tests swap in FakeQboClient
(same pattern as ticket 05's router). Ledger-Sync never writes an Account back.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from psycopg import AsyncConnection

from app.db import get_conn
from app.features.accounts import service
from app.features.auth.deps import current_user
from app.features.qbo import HttpxQboClient, QboClient
from app.features.qbo_connection.service import book_id_for_org

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


def get_qbo_client() -> QboClient:
    """The QBO port. Overridden with FakeQboClient in tests."""
    return HttpxQboClient()


async def _mirror(conn: AsyncConnection, book_id) -> dict:
    last = await service.last_synced_at(conn, book_id)
    return {
        "last_synced_at": last.isoformat() if last else None,
        "accounts": await service.list_accounts(conn, book_id),
    }


@router.get("")
async def list_chart_of_accounts(
    user: dict = Depends(current_user), conn: AsyncConnection = Depends(get_conn)
) -> dict:
    book_id = await book_id_for_org(conn, user["organization_id"])
    return await _mirror(conn, book_id)


@router.post("/refresh")
async def refresh_chart_of_accounts(
    user: dict = Depends(current_user),
    conn: AsyncConnection = Depends(get_conn),
    client: QboClient = Depends(get_qbo_client),
) -> dict:
    book_id = await book_id_for_org(conn, user["organization_id"])
    await service.refresh(conn, book_id, client)  # 409 if the Book isn't connected
    return await _mirror(conn, book_id)


@router.get("/targets")
async def categorization_targets(
    user: dict = Depends(current_user), conn: AsyncConnection = Depends(get_conn)
) -> dict:
    book_id = await book_id_for_org(conn, user["organization_id"])
    return {"targets": await service.categorization_targets(conn, book_id)}
