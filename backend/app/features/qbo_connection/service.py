"""QuickBooks connection state: OAuth persistence, on-demand token refresh, and
the push-blocked signal later tickets guard with.

Public seam for later tickets (06 Chart of Accounts, 11 Push)::

    from app.features.qbo_connection import (
        assert_connected,        # cheap guard: raise 409 unless status == connected
        get_valid_connection,    # load + refresh-if-expired -> a port Connection (or 409)
        refresh_access_token,    # rotate the access token, persist the rotated refresh token
        to_connection,           # map a stored row -> the port's Connection
    )

The stored refresh token is encrypted at rest (``crypto``); tokens are never
logged. A refresh rotates via the qbo port and persists the new encrypted token
in a *single* UPDATE — a failure never leaves the Book with no usable token
(the old, still-valid token stays put until the new one is written).
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from psycopg import AsyncConnection

from app.config import env
from app.features.qbo import Connection, QboClient, QboResult
from app.features.qbo_connection import crypto

# Refresh a little before the real expiry so an in-flight call never uses a
# token that expires mid-request.
_REFRESH_SKEW = timedelta(seconds=60)
_DEFAULT_ACCESS_TTL = 3600  # QBO access tokens last 1h; used if expires_in absent

# 409: the Book isn't connected -> pushes are blocked, reconnect required. This
# is the "push blocked" signal later tickets surface to the Admin.
_NOT_CONNECTED = HTTPException(
    status_code=409,
    detail="QuickBooks is not connected — reconnect to resume syncing.",
)


def to_connection(realm_id: str, access_token: str | None, refresh_token_encrypted) -> Connection:
    """Map a stored connection row to the qbo port's ``Connection``. Decrypts the
    refresh token; base_url from ``QBO_BASE_URL`` (default production)."""
    return Connection(
        realm_id=realm_id,
        access_token=access_token or "",
        refresh_token=crypto.decrypt(refresh_token_encrypted),
        base_url=env("QBO_BASE_URL", "https://quickbooks.api.intuit.com"),
    )


async def book_id_for_org(conn: AsyncConnection, organization_id):
    """Resolve the single Book for an Organization (created at bootstrap)."""
    cur = await conn.execute(
        "SELECT id FROM books WHERE organization_id = %s ORDER BY created_at LIMIT 1",
        (organization_id,),
    )
    row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="No Book found for this organization")
    return row[0]


async def status_for(conn: AsyncConnection, book_id) -> dict:
    """Admin-facing status. Never returns tokens. No row yet == pending."""
    cur = await conn.execute(
        "SELECT status, realm_id, connected_at FROM quickbooks_connections WHERE book_id = %s",
        (book_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return {"status": "pending", "realm_id": None, "connected_at": None}
    return {
        "status": row[0],
        "realm_id": row[1],
        "connected_at": row[2].isoformat() if row[2] else None,
    }


async def begin_authorization(conn: AsyncConnection, book_id) -> str:
    """Stamp a fresh CSRF ``state`` on the Book's connection row and return it.
    Does not downgrade an existing status, so a reconnect stays connected until
    the new callback succeeds."""
    state = secrets.token_urlsafe(32)
    await conn.execute(
        "INSERT INTO quickbooks_connections (book_id, status, oauth_state) "
        "VALUES (%s, 'pending', %s) "
        "ON CONFLICT (book_id) DO UPDATE "
        "SET oauth_state = EXCLUDED.oauth_state, updated_at = now()",
        (book_id, state),
    )
    return state


async def complete_authorization(
    conn: AsyncConnection, organization_id, state: str, realm_id: str, tokens: dict
):
    """Validate the CSRF ``state`` against a Book in this org, then persist the
    token set in a single UPDATE that flips the connection to ``connected``. The
    refresh token is encrypted before it ever touches SQL; ``oauth_state`` is
    cleared so it's single-use."""
    cur = await conn.execute(
        "SELECT qc.book_id FROM quickbooks_connections qc "
        "JOIN books b ON b.id = qc.book_id "
        "WHERE qc.oauth_state = %s AND b.organization_id = %s",
        (state, organization_id),
    )
    row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=400, detail="Invalid or expired authorization state")
    book_id = row[0]

    expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=int(tokens.get("expires_in", _DEFAULT_ACCESS_TTL))
    )
    await conn.execute(
        "UPDATE quickbooks_connections SET "
        "realm_id = %s, access_token = %s, access_token_expires_at = %s, "
        "refresh_token_encrypted = %s, status = 'connected', "
        "connected_at = now(), oauth_state = NULL, updated_at = now() "
        "WHERE book_id = %s",
        (
            realm_id,
            tokens["access_token"],
            expires_at,
            crypto.encrypt(tokens["refresh_token"]),
            book_id,
        ),
    )
    return book_id


async def _load(conn: AsyncConnection, book_id):
    cur = await conn.execute(
        "SELECT status, realm_id, access_token, access_token_expires_at, refresh_token_encrypted "
        "FROM quickbooks_connections WHERE book_id = %s",
        (book_id,),
    )
    return await cur.fetchone()


async def refresh_access_token(
    conn: AsyncConnection, book_id, client: QboClient
) -> QboResult:
    """Rotate the access token via the port and persist the rotated refresh
    token atomically. On any non-OK outcome the connection is marked
    ``disconnected`` (pushes blocked) and the old token is left untouched."""
    row = await _load(conn, book_id)
    if row is None or row[4] is None:
        raise _NOT_CONNECTED
    connection = to_connection(row[1], row[2], row[4])

    result = await client.refresh_token(connection)
    if not result.ok:
        await conn.execute(
            "UPDATE quickbooks_connections SET status = 'disconnected', updated_at = now() "
            "WHERE book_id = %s",
            (book_id,),
        )
        return result

    tokens = result.data
    expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=int(tokens.get("expires_in", _DEFAULT_ACCESS_TTL))
    )
    # Single UPDATE: encrypt in memory first, then swap the whole token set in
    # one statement — no window where the refresh-token column is half-written.
    await conn.execute(
        "UPDATE quickbooks_connections SET "
        "access_token = %s, access_token_expires_at = %s, refresh_token_encrypted = %s, "
        "status = 'connected', updated_at = now() WHERE book_id = %s",
        (tokens["access_token"], expires_at, crypto.encrypt(tokens["refresh_token"]), book_id),
    )
    return result


async def assert_connected(conn: AsyncConnection, book_id) -> None:
    """Push guard. Raises 409 (``_NOT_CONNECTED``) unless the Book's connection
    is ``connected``. Later tickets call this before a push so a disconnected or
    never-connected Book blocks and prompts the Admin to reconnect."""
    cur = await conn.execute(
        "SELECT status FROM quickbooks_connections WHERE book_id = %s", (book_id,)
    )
    row = await cur.fetchone()
    if row is None or row[0] != "connected":
        raise _NOT_CONNECTED


async def get_valid_connection(
    conn: AsyncConnection, book_id, client: QboClient
) -> Connection:
    """The push path's entry point: return a port ``Connection`` whose access
    token is fresh, refreshing on demand when it's expired (or about to). Raises
    409 if the Book isn't connected or a needed refresh fails."""
    row = await _load(conn, book_id)
    if row is None or row[0] != "connected" or row[4] is None:
        raise _NOT_CONNECTED

    _status, realm_id, access_token, expires_at, refresh_encrypted = row
    if expires_at is None or expires_at <= datetime.now(timezone.utc) + _REFRESH_SKEW:
        result = await refresh_access_token(conn, book_id, client)
        if not result.ok:
            raise _NOT_CONNECTED
        row = await _load(conn, book_id)
        _status, realm_id, access_token, expires_at, refresh_encrypted = row

    return to_connection(realm_id, access_token, refresh_encrypted)
