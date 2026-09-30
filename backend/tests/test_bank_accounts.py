"""Ticket 07: Bank Account creation + one-to-one mapping.

An Admin creates a Bank Account and maps it to exactly one *active* Account from
the ticket-06 mirror (required — no empty/absent/inactive target), re-maps it,
and the setup list shows each Bank Account with its mapped Account. Every
create/map route is Admin-only. Also exercises the reusable seam tickets 08/10
consume: ``list_bank_accounts`` / ``get_bank_account``.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

from app.db import get_conn
from app.features.accounts import service as accounts_service
from app.features.auth import service as auth_service
from app.features.bank_accounts import get_bank_account, list_bank_accounts
from app.features.qbo import FakeQboClient
from app.features.qbo_connection import crypto
from app.main import app

# --- mirror fixtures (QBO Account dict shape) -----------------------------

CHECKING = {"Id": "1", "Name": "Checking", "AccountType": "Bank",
            "AccountSubType": "Checking", "Classification": "Asset",
            "AcctNum": "1000", "Active": True, "SyncToken": "0"}
CARD = {"Id": "2", "Name": "Business Card", "AccountType": "Credit Card",
        "Classification": "Liability", "AcctNum": "2000", "Active": True, "SyncToken": "0"}
CLOSED = {"Id": "99", "Name": "Closed Savings", "AccountType": "Bank",
          "Classification": "Asset", "Active": False, "SyncToken": "0"}

DEFAULT_ACCOUNTS = [CHECKING, CARD, CLOSED]


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
    async def _use_pool():
        async with pool.connection() as conn:
            yield conn

    app.dependency_overrides[get_conn] = _use_pool
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as client:
        yield SimpleNamespace(client=client, pool=pool)
    app.dependency_overrides.clear()


async def _connected_book(pool, *, role="Admin", client=None):
    """Bootstrap, log a user of `role` in, seed a connected QBO connection.
    Returns book_id and sets the session cookie on `client`."""
    async with pool.connection() as conn:
        await auth_service.bootstrap(conn)
    async with pool.connection() as conn:
        org_id = (await (await conn.execute("SELECT id FROM organizations LIMIT 1")).fetchone())[0]
        book_id = (await (await conn.execute(
            "SELECT id FROM books WHERE organization_id = %s LIMIT 1", (org_id,)
        )).fetchone())[0]
        if role == "Admin":
            user_id = (await (await conn.execute(
                "SELECT id FROM users WHERE role='Admin' LIMIT 1")).fetchone())[0]
        else:
            user_id = (await (await conn.execute(
                "INSERT INTO users (organization_id, email, role, must_set_password) "
                "VALUES (%s, %s, %s, false) RETURNING id",
                (org_id, f"{role.lower()}@example.com", role),
            )).fetchone())[0]
        sid = await auth_service.create_session(conn, user_id)
        await conn.execute(
            "INSERT INTO quickbooks_connections "
            "(book_id, realm_id, access_token, access_token_expires_at, "
            " refresh_token_encrypted, status, connected_at) "
            "VALUES (%s, '9999', 'access-tok', %s, %s, 'connected', now())",
            (book_id, datetime.now(timezone.utc) + timedelta(hours=1),
             crypto.encrypt("refresh-tok")),
        )
    if client is not None:
        client.cookies.set("session", auth_service.sign_cookie(sid))
    return book_id


async def _setup(pool, client, *, role="Admin", accounts=DEFAULT_ACCOUNTS):
    """Connected Book with the mirror seeded, and `client` logged in as `role`."""
    book_id = await _connected_book(pool, role=role, client=client)
    async with pool.connection() as conn:
        await accounts_service.refresh(conn, book_id, FakeQboClient(list(accounts)))
    return book_id


# --- create + map ----------------------------------------------------------


async def test_admin_creates_and_maps_to_active_account(ctx):
    await _setup(ctx.pool, ctx.client, role="Admin")

    r = await ctx.client.post(
        "/api/bank-accounts", json={"name": "Ops Checking", "qbo_account_id": "1"}
    )
    assert r.status_code == 201
    ba = r.json()["bank_accounts"]
    assert len(ba) == 1
    assert ba[0]["name"] == "Ops Checking"
    assert ba[0]["qbo_account_id"] == "1"
    assert ba[0]["mapped_account"] == {"qbo_id": "1", "name": "Checking", "active": True}


async def test_create_without_a_mapping_is_rejected(ctx):
    book_id = await _setup(ctx.pool, ctx.client, role="Admin")

    # mapping omitted entirely -> pydantic 422
    assert (await ctx.client.post(
        "/api/bank-accounts", json={"name": "No Map"})).status_code == 422
    # mapping present but blank -> rejected
    assert (await ctx.client.post(
        "/api/bank-accounts", json={"name": "Blank", "qbo_account_id": ""})).status_code == 422

    async with ctx.pool.connection() as conn:
        assert await list_bank_accounts(conn, book_id) == []  # nothing persisted


async def test_mapping_to_nonexistent_or_inactive_account_is_rejected(ctx):
    book_id = await _setup(ctx.pool, ctx.client, role="Admin")

    # unknown qbo_id
    assert (await ctx.client.post(
        "/api/bank-accounts", json={"name": "Ghost", "qbo_account_id": "does-not-exist"}
    )).status_code == 422
    # inactive account in the mirror (CLOSED, id=99)
    assert (await ctx.client.post(
        "/api/bank-accounts", json={"name": "Old", "qbo_account_id": "99"}
    )).status_code == 422

    async with ctx.pool.connection() as conn:
        assert await list_bank_accounts(conn, book_id) == []


async def test_remap_by_admin_updates_the_mapping(ctx):
    await _setup(ctx.pool, ctx.client, role="Admin")
    created = (await ctx.client.post(
        "/api/bank-accounts", json={"name": "Checking", "qbo_account_id": "1"}
    )).json()["bank_accounts"][0]

    r = await ctx.client.patch(
        f"/api/bank-accounts/{created['id']}", json={"qbo_account_id": "2"}
    )
    assert r.status_code == 200
    updated = r.json()["bank_accounts"][0]
    assert updated["qbo_account_id"] == "2"
    assert updated["mapped_account"] == {"qbo_id": "2", "name": "Business Card", "active": True}

    # re-map to an inactive account is rejected; mapping unchanged
    assert (await ctx.client.patch(
        f"/api/bank-accounts/{created['id']}", json={"qbo_account_id": "99"}
    )).status_code == 422


async def test_setup_list_shows_each_bank_account_and_its_mapped_account(ctx):
    await _setup(ctx.pool, ctx.client, role="Admin")
    await ctx.client.post("/api/bank-accounts", json={"name": "Checking", "qbo_account_id": "1"})
    await ctx.client.post("/api/bank-accounts", json={"name": "Card", "qbo_account_id": "2"})

    r = await ctx.client.get("/api/bank-accounts")
    assert r.status_code == 200
    body = r.json()
    by_name = {b["name"]: b for b in body["bank_accounts"]}
    assert by_name["Checking"]["mapped_account"]["name"] == "Checking"
    assert by_name["Card"]["mapped_account"]["name"] == "Business Card"
    # the create/map picker offers active accounts only (CLOSED id=99 excluded)
    assert {a["qbo_id"] for a in body["accounts"]} == {"1", "2"}


# --- role + auth guards ----------------------------------------------------


async def test_bookkeeper_is_forbidden_on_every_route(ctx):
    book_id = await _setup(ctx.pool, ctx.client, role="Bookkeeper")
    async with ctx.pool.connection() as conn:
        ba_id = (await (await conn.execute(
            "INSERT INTO bank_accounts (book_id, name, qbo_account_id) "
            "VALUES (%s, 'Seeded', '1') RETURNING id", (book_id,)
        )).fetchone())[0]

    assert (await ctx.client.get("/api/bank-accounts")).status_code == 403
    assert (await ctx.client.post(
        "/api/bank-accounts", json={"name": "X", "qbo_account_id": "1"})).status_code == 403
    assert (await ctx.client.patch(
        f"/api/bank-accounts/{ba_id}", json={"qbo_account_id": "2"})).status_code == 403


async def test_routes_require_authentication(ctx):
    assert (await ctx.client.get("/api/bank-accounts")).status_code == 401
    assert (await ctx.client.post(
        "/api/bank-accounts", json={"name": "X", "qbo_account_id": "1"})).status_code == 401


# --- reusable seam (tickets 08/10) -----------------------------------------


async def test_seam_functions_return_row_with_qbo_account_id(ctx):
    book_id = await _setup(ctx.pool, ctx.client, role="Admin")
    created = (await ctx.client.post(
        "/api/bank-accounts", json={"name": "Checking", "qbo_account_id": "1"}
    )).json()["bank_accounts"][0]

    async with ctx.pool.connection() as conn:
        rows = await list_bank_accounts(conn, book_id)
        assert len(rows) == 1
        assert rows[0]["name"] == "Checking"
        assert rows[0]["qbo_account_id"] == "1"

        one = await get_bank_account(conn, created["id"])
        assert one["qbo_account_id"] == "1"
        assert await get_bank_account(
            conn, "00000000-0000-0000-0000-000000000000"
        ) is None
