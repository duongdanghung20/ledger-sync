"""Ticket 05: QuickBooks connection — OAuth callback, encrypted tokens at rest,
on-demand refresh with atomic rotation, disconnect-on-failure, Admin-only."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from fastapi import HTTPException

from app.db import get_conn
from app.features.auth import service as auth_service
from app.features.qbo import FakeQboClient, Outcome, QboResult
from app.features.qbo_connection import crypto, service
from app.features.qbo_connection.router import get_code_exchanger, get_qbo_client
from app.main import app

EXCH_REFRESH = "exch-refresh-SECRET"  # plaintext we assert never lands in the DB


async def _fake_exchange(code: str) -> QboResult:
    """Stand-in for the one-time authorization_code -> tokens exchange."""
    return QboResult.of(
        Outcome.OK,
        data={
            "access_token": "exch-access",
            "refresh_token": EXCH_REFRESH,
            "expires_in": 3600,
            "x_refresh_token_expires_in": 8726400,
            "token_type": "bearer",
        },
    )


@pytest.fixture(autouse=True)
def qbo_env(monkeypatch):
    monkeypatch.setenv("TOKEN_ENC_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("QBO_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("QBO_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("APP_BASE_URL", "http://localhost:8080")
    monkeypatch.setenv("SESSION_SECRET", "test-secret-please-change")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setenv("ORG_NAME", "Acme Books")


@pytest_asyncio.fixture
async def ctx(pool):
    fake = FakeQboClient()

    async def _use_pool():
        async with pool.connection() as conn:
            yield conn

    app.dependency_overrides[get_conn] = _use_pool
    app.dependency_overrides[get_qbo_client] = lambda: fake
    app.dependency_overrides[get_code_exchanger] = lambda: _fake_exchange

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as client:
        yield SimpleNamespace(client=client, fake=fake, pool=pool)
    app.dependency_overrides.clear()


# --- helpers --------------------------------------------------------------


async def _bootstrap(pool):
    async with pool.connection() as conn:
        await auth_service.bootstrap(conn)


async def _login_as(pool, client, role: str):
    """Create/reuse a user with `role`, open a session, set the signed cookie.
    Returns (organization_id, book_id)."""
    async with pool.connection() as conn:
        org_id = (await (await conn.execute("SELECT id FROM organizations LIMIT 1")).fetchone())[0]
        book_id = (
            await (
                await conn.execute(
                    "SELECT id FROM books WHERE organization_id = %s LIMIT 1", (org_id,)
                )
            ).fetchone()
        )[0]
        if role == "Admin":
            user_id = (
                await (await conn.execute("SELECT id FROM users WHERE role='Admin' LIMIT 1")).fetchone()
            )[0]
        else:
            user_id = (
                await (
                    await conn.execute(
                        "INSERT INTO users (organization_id, email, role, must_set_password) "
                        "VALUES (%s, %s, %s, false) RETURNING id",
                        (org_id, f"{role.lower()}@example.com", role),
                    )
                ).fetchone()
            )[0]
        sid = await auth_service.create_session(conn, user_id)
    client.cookies.set("session", auth_service.sign_cookie(sid))
    return org_id, book_id


async def _seed_connected(pool, book_id, *, refresh="orig-refresh", expired=True):
    expires_at = datetime.now(timezone.utc) + (
        timedelta(minutes=-5) if expired else timedelta(hours=1)
    )
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO quickbooks_connections "
            "(book_id, realm_id, access_token, access_token_expires_at, "
            " refresh_token_encrypted, status, connected_at) "
            "VALUES (%s, '9999', 'old-access', %s, %s, 'connected', now())",
            (book_id, expires_at, crypto.encrypt(refresh)),
        )


async def _row(pool, book_id):
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT status, realm_id, access_token, refresh_token_encrypted "
            "FROM quickbooks_connections WHERE book_id = %s",
            (book_id,),
        )
        return await cur.fetchone()


# --- OAuth callback: encrypted-at-rest ------------------------------------


async def test_callback_records_realm_and_stores_encrypted_refresh_token(ctx):
    await _bootstrap(ctx.pool)
    _org, book_id = await _login_as(ctx.pool, ctx.client, "Admin")

    r = await ctx.client.get("/api/qbo/authorize")
    assert r.status_code == 307
    location = r.headers["location"]
    assert location.startswith("https://appcenter.intuit.com/connect/oauth2")
    state = parse_qs(urlparse(location).query)["state"][0]
    # redirect_uri must be exactly {APP_BASE_URL}/api/qbo/callback
    assert parse_qs(urlparse(location).query)["redirect_uri"][0] == (
        "http://localhost:8080/api/qbo/callback"
    )

    cb = await ctx.client.get(f"/api/qbo/callback?code=auth-code&state={state}&realmId=9999")
    assert cb.status_code == 303
    assert "connected=1" in cb.headers["location"]

    status, realm_id, access_token, refresh_encrypted = await _row(ctx.pool, book_id)
    assert status == "connected"
    assert realm_id == "9999"
    assert access_token == "exch-access"
    # Stored bytes are NOT the plaintext refresh token ...
    assert bytes(refresh_encrypted) != EXCH_REFRESH.encode()
    assert EXCH_REFRESH.encode() not in bytes(refresh_encrypted)
    # ... but decrypt round-trips.
    assert crypto.decrypt(refresh_encrypted) == EXCH_REFRESH


async def test_invalid_state_is_rejected(ctx):
    await _bootstrap(ctx.pool)
    await _login_as(ctx.pool, ctx.client, "Admin")
    await ctx.client.get("/api/qbo/authorize")  # creates a real state

    cb = await ctx.client.get("/api/qbo/callback?code=c&state=not-the-real-state&realmId=9999")
    assert cb.status_code == 400  # CSRF / stale state


# --- on-demand refresh: rotate + persist atomically -----------------------


async def test_on_demand_refresh_rotates_and_persists_atomically(ctx):
    await _bootstrap(ctx.pool)
    _org, book_id = await _login_as(ctx.pool, ctx.client, "Admin")
    await _seed_connected(ctx.pool, book_id, refresh="orig-refresh", expired=True)

    async with ctx.pool.connection() as conn:
        result = await service.refresh_access_token(conn, book_id, ctx.fake)
    assert result.ok

    status, _realm, access_token, refresh_encrypted = await _row(ctx.pool, book_id)
    assert status == "connected"
    assert access_token == "fake-access-1"            # new access token
    rotated = crypto.decrypt(refresh_encrypted)
    assert rotated == "fake-refresh-1"                # rotated ...
    assert rotated != "orig-refresh"                  # ... away from the old one
    assert bytes(refresh_encrypted) != rotated.encode()  # and still encrypted

    # get_valid_connection returns a live Connection with the fresh access token.
    await _seed_reset_expired(ctx.pool, book_id)
    async with ctx.pool.connection() as conn:
        connection = await service.get_valid_connection(conn, book_id, ctx.fake)
    assert connection.access_token == "fake-access-2"
    assert connection.realm_id == "9999"


async def _seed_reset_expired(pool, book_id):
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE quickbooks_connections SET access_token_expires_at = %s WHERE book_id = %s",
            (datetime.now(timezone.utc) - timedelta(minutes=5), book_id),
        )


# --- refresh failure -> disconnected -> push blocked ----------------------


async def test_refresh_failure_disconnects_and_blocks_push(ctx):
    await _bootstrap(ctx.pool)
    _org, book_id = await _login_as(ctx.pool, ctx.client, "Admin")
    await _seed_connected(ctx.pool, book_id, refresh="orig-refresh", expired=True)

    ctx.fake.fail_next(Outcome.SERVER_ERROR)
    async with ctx.pool.connection() as conn:
        result = await service.refresh_access_token(conn, book_id, ctx.fake)
    assert not result.ok

    status, _realm, _access, refresh_encrypted = await _row(ctx.pool, book_id)
    assert status == "disconnected"
    # The old, still-valid refresh token is left untouched (never lost).
    assert crypto.decrypt(refresh_encrypted) == "orig-refresh"

    # Push is blocked: the signal later tickets guard with raises 409.
    async with ctx.pool.connection() as conn:
        with pytest.raises(HTTPException) as exc:
            await service.assert_connected(conn, book_id)
    assert exc.value.status_code == 409


# --- status values --------------------------------------------------------


async def test_status_transitions_pending_connected_disconnected(ctx):
    await _bootstrap(ctx.pool)
    _org, book_id = await _login_as(ctx.pool, ctx.client, "Admin")

    async def _status():
        return (await ctx.client.get("/api/qbo/status")).json()["status"]

    assert await _status() == "pending"  # no row yet

    state = parse_qs(urlparse((await ctx.client.get("/api/qbo/authorize")).headers["location"]).query)["state"][0]
    await ctx.client.get(f"/api/qbo/callback?code=c&state={state}&realmId=9999")
    assert await _status() == "connected"

    ctx.fake.fail_next(Outcome.NETWORK)
    async with ctx.pool.connection() as conn:
        await service.refresh_access_token(conn, book_id, ctx.fake)
    assert await _status() == "disconnected"


async def test_reconnect_restores_connected(ctx):
    await _bootstrap(ctx.pool)
    _org, book_id = await _login_as(ctx.pool, ctx.client, "Admin")
    await _seed_connected(ctx.pool, book_id)
    async with ctx.pool.connection() as conn:
        await conn.execute(
            "UPDATE quickbooks_connections SET status='disconnected' WHERE book_id=%s", (book_id,)
        )

    # Reconnect uses the same flow.
    state = parse_qs(urlparse((await ctx.client.get("/api/qbo/authorize")).headers["location"]).query)["state"][0]
    cb = await ctx.client.get(f"/api/qbo/callback?code=c&state={state}&realmId=9999")
    assert cb.status_code == 303
    assert (await _row(ctx.pool, book_id))[0] == "connected"


# --- Admin-only -----------------------------------------------------------


async def test_connection_routes_are_admin_only(ctx):
    await _bootstrap(ctx.pool)
    await _login_as(ctx.pool, ctx.client, "Bookkeeper")

    assert (await ctx.client.get("/api/qbo/status")).status_code == 403
    assert (await ctx.client.get("/api/qbo/authorize")).status_code == 403
    cb = await ctx.client.get("/api/qbo/callback?code=c&state=s&realmId=9999")
    assert cb.status_code == 403


async def test_routes_require_authentication(ctx):
    assert (await ctx.client.get("/api/qbo/status")).status_code == 401


# --- crypto unit ----------------------------------------------------------


async def test_encrypt_is_authenticated_and_round_trips():
    token = crypto.encrypt("s3cr3t-refresh")
    assert isinstance(token, bytes)
    assert token != b"s3cr3t-refresh"
    assert crypto.decrypt(token) == "s3cr3t-refresh"

    from cryptography.fernet import InvalidToken

    with pytest.raises(InvalidToken):  # tamper detection (authenticated)
        crypto.decrypt(token[:-1] + bytes([token[-1] ^ 0x01]))
