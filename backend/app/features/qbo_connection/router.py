"""QuickBooks connection HTTP surface (Admin-only).

Routes sit under ``/api/qbo`` so the OAuth callback is exactly
``{APP_BASE_URL}/api/qbo/callback`` (the URI registered with Intuit).

- GET /api/qbo/status     current status (never tokens)
- GET /api/qbo/authorize  start the authorization-code flow -> redirect to Intuit
- GET /api/qbo/callback   Intuit redirects here with code + state + realmId

The qbo client and the one-time code exchanger are injected so tests can swap in
the fake / a stub without any network.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from psycopg import AsyncConnection

from app.config import env
from app.db import get_conn
from app.features.auth.deps import require_role
from app.features.qbo import HttpxQboClient, QboClient
from app.features.qbo_connection import oauth, service

router = APIRouter(prefix="/api/qbo", tags=["qbo-connection"])

_admin = require_role("Admin")


def get_qbo_client() -> QboClient:
    """The QBO port. Overridden with FakeQboClient in tests."""
    return HttpxQboClient()


def get_code_exchanger():
    """The one-time authorization_code -> tokens exchanger. Overridden in tests."""
    return oauth.exchange_code


def _frontend_status_url() -> str:
    base = env("APP_BASE_URL", "http://localhost:8080").rstrip("/")
    return f"{base}/settings/quickbooks"


@router.get("/status")
async def connection_status(
    user: dict = Depends(_admin), conn: AsyncConnection = Depends(get_conn)
) -> dict:
    book_id = await service.book_id_for_org(conn, user["organization_id"])
    return await service.status_for(conn, book_id)


@router.get("/authorize")
async def authorize(
    user: dict = Depends(_admin), conn: AsyncConnection = Depends(get_conn)
) -> RedirectResponse:
    book_id = await service.book_id_for_org(conn, user["organization_id"])
    state = await service.begin_authorization(conn, book_id)
    return RedirectResponse(oauth.authorization_url(state), status_code=307)


@router.get("/callback")
async def callback(
    code: str,
    state: str,
    realmId: str,
    user: dict = Depends(_admin),
    conn: AsyncConnection = Depends(get_conn),
    exchange=Depends(get_code_exchanger),
) -> RedirectResponse:
    result = await exchange(code)
    if not result.ok:
        return RedirectResponse(f"{_frontend_status_url()}?error=exchange", status_code=303)
    await service.complete_authorization(
        conn, user["organization_id"], state, realmId, result.data
    )
    return RedirectResponse(f"{_frontend_status_url()}?connected=1", status_code=303)
