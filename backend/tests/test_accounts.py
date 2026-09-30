"""Ticket 06: Chart-of-Accounts mirror — reconcile by qbo_id (rename/retype in
place, absent -> inactive never deleted, returning not duplicated), keeps
inactive accounts, categorization targets exclude AR/AP + inactive grouped by
Classification, and last_synced_at is stamped on refresh."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from fastapi import HTTPException

from app.db import get_conn
from app.features.accounts import service
from app.features.accounts.router import get_qbo_client
from app.features.auth import service as auth_service
from app.features.qbo import FakeQboClient
from app.features.qbo_connection import crypto
from app.main import app

# --- account fixtures (QBO Account dict shape) ----------------------------

CHECKING = {"Id": "1", "Name": "Checking", "AccountType": "Bank",
            "AccountSubType": "Checking", "Classification": "Asset",
            "AcctNum": "1000", "Active": True, "SyncToken": "0"}
SUPPLIES = {"Id": "2", "Name": "Office Supplies", "AccountType": "Expense",
            "Classification": "Expense", "Active": True, "SyncToken": "1"}
OLD_PETTY = {"Id": "99", "Name": "Old Petty Cash", "AccountType": "Bank",
             "Classification": "Asset", "Active": False, "SyncToken": "2"}
INCOME = {"Id": "3", "Name": "Sales", "AccountType": "Income",
          "Classification": "Revenue", "Active": True, "SyncToken": "0"}
AR = {"Id": "7", "Name": "Accounts Receivable", "AccountType": "Accounts Receivable",
      "Classification": "Asset", "Active": True, "SyncToken": "0"}
AP = {"Id": "8", "Name": "Accounts Payable", "AccountType": "Accounts Payable",
      "Classification": "Liability", "Active": True, "SyncToken": "0"}


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

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as client:
        yield SimpleNamespace(client=client, fake=fake, pool=pool)
    app.dependency_overrides.clear()


# --- helpers --------------------------------------------------------------


async def _connected_book(pool, *, role="Bookkeeper", client=None):
    """Bootstrap, log a user of `role` in, and seed a connected QBO connection.
    Returns (book_id,) and sets the session cookie on `client`."""
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
        # A connected connection with a non-expired access token so
        # get_valid_connection needs no refresh round-trip.
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


async def _rows(pool, book_id):
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT qbo_id, name, account_type, active FROM accounts "
            "WHERE book_id = %s ORDER BY qbo_id",
            (book_id,),
        )
        return await cur.fetchall()


# --- reconcile / refresh (service level, full control over the CoA) -------


async def test_initial_fetch_mirrors_active_and_inactive_and_stamps_synced(ctx):
    book_id = await _connected_book(ctx.pool)

    async with ctx.pool.connection() as conn:
        assert await service.last_synced_at(conn, book_id) is None  # never synced
        result = await service.refresh(conn, book_id, FakeQboClient([CHECKING, OLD_PETTY]))
    assert result.ok

    rows = await _rows(ctx.pool, book_id)
    by_id = {r[0]: r for r in rows}
    assert set(by_id) == {"1", "99"}
    assert by_id["1"][3] is True                 # active mirrored
    assert by_id["99"][3] is False               # inactive mirrored (not dropped)

    async with ctx.pool.connection() as conn:
        assert await service.last_synced_at(conn, book_id) is not None  # stamped


async def test_rename_and_retype_update_in_place_no_duplicate(ctx):
    book_id = await _connected_book(ctx.pool)
    async with ctx.pool.connection() as conn:
        await service.refresh(conn, book_id, FakeQboClient([CHECKING]))
        renamed = {**CHECKING, "Name": "Main Checking", "AccountType": "Other Current Asset",
                   "SyncToken": "5"}
        await service.refresh(conn, book_id, FakeQboClient([renamed]))

    rows = await _rows(ctx.pool, book_id)
    assert len(rows) == 1                        # no duplicate row for qbo_id=1
    assert rows[0] == ("1", "Main Checking", "Other Current Asset", True)


async def test_absent_account_becomes_inactive_not_deleted(ctx):
    book_id = await _connected_book(ctx.pool)
    async with ctx.pool.connection() as conn:
        await service.refresh(conn, book_id, FakeQboClient([CHECKING, SUPPLIES]))
        await service.refresh(conn, book_id, FakeQboClient([CHECKING]))  # SUPPLIES dropped

    rows = await _rows(ctx.pool, book_id)
    by_id = {r[0]: r for r in rows}
    assert set(by_id) == {"1", "2"}              # SUPPLIES still present ...
    assert by_id["2"][3] is False                # ... but marked inactive
    assert by_id["1"][3] is True


async def test_returning_account_reactivates_same_row(ctx):
    book_id = await _connected_book(ctx.pool)
    async with ctx.pool.connection() as conn:
        await service.refresh(conn, book_id, FakeQboClient([CHECKING, SUPPLIES]))
        await service.refresh(conn, book_id, FakeQboClient([CHECKING]))          # SUPPLIES gone
        await service.refresh(conn, book_id, FakeQboClient([CHECKING, SUPPLIES]))  # SUPPLIES back

    rows = await _rows(ctx.pool, book_id)
    by_id = {r[0]: r for r in rows}
    assert set(by_id) == {"1", "2"}              # not duplicated on return
    assert by_id["2"][3] is True                 # reactivated in place


async def test_failed_fetch_leaves_mirror_untouched(ctx):
    from app.features.qbo import Outcome

    book_id = await _connected_book(ctx.pool)
    async with ctx.pool.connection() as conn:
        await service.refresh(conn, book_id, FakeQboClient([CHECKING]))
        fake = FakeQboClient([SUPPLIES])
        fake.fail_next(Outcome.SERVER_ERROR)
        result = await service.refresh(conn, book_id, fake)
    assert not result.ok
    rows = await _rows(ctx.pool, book_id)
    assert {r[0] for r in rows} == {"1"}         # nothing wiped, nothing added


async def test_refresh_if_stale_skips_when_fresh_refreshes_when_stale(ctx):
    book_id = await _connected_book(ctx.pool)
    async with ctx.pool.connection() as conn:
        # never synced -> stale -> refreshes
        assert await service.refresh_if_stale(conn, book_id, FakeQboClient([CHECKING])) is True
        # just synced -> fresh -> skips (no second fetch)
        assert await service.refresh_if_stale(conn, book_id, FakeQboClient([CHECKING, SUPPLIES])) is False
    assert {r[0] for r in await _rows(ctx.pool, book_id)} == {"1"}

    # force stale, then it refreshes and picks up the new account
    async with ctx.pool.connection() as conn:
        await conn.execute(
            "UPDATE books SET last_synced_at = %s WHERE id = %s",
            (datetime.now(timezone.utc) - timedelta(hours=1), book_id),
        )
        assert await service.refresh_if_stale(conn, book_id, FakeQboClient([CHECKING, SUPPLIES])) is True
    assert {r[0] for r in await _rows(ctx.pool, book_id)} == {"1", "2"}


async def test_refresh_on_unconnected_book_blocks_409(ctx):
    # bootstrap + book but NO quickbooks_connections row -> not connected
    async with ctx.pool.connection() as conn:
        await auth_service.bootstrap(conn)
        org_id = (await (await conn.execute("SELECT id FROM organizations LIMIT 1")).fetchone())[0]
        book_id = (await (await conn.execute(
            "SELECT id FROM books WHERE organization_id = %s LIMIT 1", (org_id,)
        )).fetchone())[0]
    async with ctx.pool.connection() as conn:
        with pytest.raises(HTTPException) as exc:
            await service.refresh(conn, book_id, FakeQboClient([CHECKING]))
    assert exc.value.status_code == 409


# --- categorization targets -----------------------------------------------


async def test_targets_exclude_ar_ap_and_inactive_grouped_by_classification(ctx):
    book_id = await _connected_book(ctx.pool)
    async with ctx.pool.connection() as conn:
        await service.refresh(
            conn, book_id, FakeQboClient([CHECKING, INCOME, AR, AP, OLD_PETTY])
        )
        targets = await service.categorization_targets(conn, book_id)

    # grouped by Classification; AR/AP types excluded; inactive OLD_PETTY excluded
    assert set(targets) == {"Asset", "Revenue"}
    assert [a["qbo_id"] for a in targets["Asset"]] == ["1"]      # Checking only (not AR, not OLD)
    assert [a["qbo_id"] for a in targets["Revenue"]] == ["3"]    # Sales
    all_types = [a["account_type"] for group in targets.values() for a in group]
    assert "Accounts Receivable" not in all_types
    assert "Accounts Payable" not in all_types


# --- HTTP surface ---------------------------------------------------------


async def test_endpoints_refresh_list_and_targets(ctx):
    app.dependency_overrides[get_qbo_client] = lambda: FakeQboClient([CHECKING, INCOME, AR, OLD_PETTY])
    await _connected_book(ctx.pool, role="Bookkeeper", client=ctx.client)

    r = await ctx.client.post("/api/accounts/refresh")
    assert r.status_code == 200
    body = r.json()
    assert body["last_synced_at"] is not None
    assert {a["qbo_id"] for a in body["accounts"]} == {"1", "3", "7", "99"}  # incl. inactive

    r = await ctx.client.get("/api/accounts")
    assert r.status_code == 200
    assert {a["qbo_id"] for a in r.json()["accounts"]} == {"1", "3", "7", "99"}

    r = await ctx.client.get("/api/accounts/targets")
    assert r.status_code == 200
    targets = r.json()["targets"]
    assert set(targets) == {"Asset", "Revenue"}                 # no AR (Asset drops to Checking)
    assert [a["qbo_id"] for a in targets["Asset"]] == ["1"]


async def test_routes_require_authentication(ctx):
    assert (await ctx.client.get("/api/accounts")).status_code == 401
    assert (await ctx.client.post("/api/accounts/refresh")).status_code == 401
    assert (await ctx.client.get("/api/accounts/targets")).status_code == 401


async def test_bookkeeper_is_allowed(ctx):
    """Chart-of-Accounts read/refresh is Bookkeeper-allowed (not Admin-only)."""
    app.dependency_overrides[get_qbo_client] = lambda: FakeQboClient([CHECKING])
    await _connected_book(ctx.pool, role="Bookkeeper", client=ctx.client)
    assert (await ctx.client.get("/api/accounts")).status_code == 200
    assert (await ctx.client.post("/api/accounts/refresh")).status_code == 200
