"""Ticket 12: the /api/review read model derives state, never stores it.

Seeds a Book with a connected fake QBO + mirror (same pattern as test_push) and a
transaction in each of the five states, then asserts GET /api/review derives them
correctly and offers the active categorization targets. Also asserts the money-path
guard the UI mirrors: approval is barred for an uncategorized transaction.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from psycopg.types.json import Jsonb

from app.db import get_conn
from app.features.auth import service as auth_service
from app.features.push import service as push_service
from app.features.push.router import get_qbo_client
from app.features.qbo import FakeQboClient, Outcome
from app.features.qbo_connection import crypto
from app.main import app


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("TOKEN_ENC_KEY", Fernet.generate_key().decode())
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


async def _setup(ctx):
    """Bootstrap, log in a Bookkeeper, seed a connected QBO connection + a mirror
    with an active cash account + an active + an inactive expense account + a bank
    account mapped to the cash side. Returns (book_id, bank_account_id)."""
    async with ctx.pool.connection() as conn:
        await auth_service.bootstrap(conn)
    async with ctx.pool.connection() as conn:
        org_id = (await (await conn.execute("SELECT id FROM organizations LIMIT 1")).fetchone())[0]
        book_id = (await (await conn.execute(
            "SELECT id FROM books WHERE organization_id = %s LIMIT 1", (org_id,)
        )).fetchone())[0]
        user_id = (await (await conn.execute(
            "INSERT INTO users (organization_id, email, role, must_set_password) "
            "VALUES (%s, 'bk@example.com', 'Bookkeeper', false) RETURNING id", (org_id,)
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
        await _account(conn, book_id, "cash-1", "Checking", atype="Bank")
        await _account(conn, book_id, "exp-office", "Office")
        await _account(conn, book_id, "exp-old", "Old", active=False)
        ba = (await (await conn.execute(
            "INSERT INTO bank_accounts (book_id, name, qbo_account_id) "
            "VALUES (%s, 'Checking', 'cash-1') RETURNING id", (book_id,)
        )).fetchone())[0]
        await conn.execute("UPDATE books SET last_synced_at = now() WHERE id = %s", (book_id,))
    ctx.client.cookies.set("session", auth_service.sign_cookie(sid))
    return book_id, ba


async def _account(conn, book_id, qbo_id, name, *, active=True, atype="Expense"):
    await conn.execute(
        "INSERT INTO accounts (book_id, qbo_id, name, account_type, classification, active) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (book_id, qbo_id, name, atype, atype, active),
    )


async def _tx(ctx, book_id, ba, *, description, category=None):
    async with ctx.pool.connection() as conn:
        row = await (await conn.execute(
            "INSERT INTO imported_transactions "
            "(book_id, bank_account_id, date, amount, description, raw, dedup_key, "
            " assigned_account_qbo_id, category_source) "
            "VALUES (%s, %s, '2026-09-02', %s, %s, %s, %s, %s, %s) RETURNING id",
            (book_id, ba, Decimal("-9.00"), description, Jsonb({}), description,
             category, "manual" if category else "none"),
        )).fetchone()
    return str(row[0])


def _by_desc(rows, description):
    return next(r for r in rows if r["description"] == description)


async def test_review_derives_all_five_states(ctx):
    book_id, ba = await _setup(ctx)

    await _tx(ctx, book_id, ba, description="UNCAT")                       # uncategorized
    await _tx(ctx, book_id, ba, description="CAT", category="exp-office")  # categorized
    approved = await _tx(ctx, book_id, ba, description="APPROVED", category="exp-office")
    posted = await _tx(ctx, book_id, ba, description="POSTED", category="exp-office")
    failed = await _tx(ctx, book_id, ba, description="FAILED", category="exp-office")

    # push_book drives EVERY pending entry, so sequence approvals around each push
    # to land one row in each terminal state deterministically.
    async def _approve(tx_id):
        r = await ctx.client.post(f"/api/journal/transactions/{tx_id}/approve")
        assert r.status_code == 201, r.text

    async def _push():
        async with ctx.pool.connection() as conn:
            await push_service.push_book(conn, book_id, ctx.fake)

    await _approve(posted)          # posted: approve then a clean push
    await _push()
    await _approve(failed)          # failed: approve then a push whose post is forced 4xx
    ctx.fake.fail_next(Outcome.CLIENT_ERROR, times=1)
    await _push()
    await _approve(approved)        # approved: pending, never pushed

    rows = (await ctx.client.get("/api/review")).json()["transactions"]

    assert _by_desc(rows, "UNCAT")["state"] == "uncategorized"
    assert _by_desc(rows, "UNCAT")["journal_entry"] is None
    assert _by_desc(rows, "UNCAT")["assigned_account_qbo_id"] is None

    assert _by_desc(rows, "CAT")["state"] == "categorized"
    assert _by_desc(rows, "CAT")["assigned_account_qbo_id"] == "exp-office"
    assert _by_desc(rows, "CAT")["journal_entry"] is None

    ar = _by_desc(rows, "APPROVED")
    assert ar["state"] == "approved"
    assert ar["journal_entry"]["sync_status"] == "pending"
    assert ar["journal_entry"]["doc_number"] is not None

    assert _by_desc(rows, "POSTED")["state"] == "posted"
    assert _by_desc(rows, "POSTED")["journal_entry"]["sync_status"] == "posted"

    fr = _by_desc(rows, "FAILED")
    assert fr["state"] == "failed"
    assert fr["journal_entry"]["last_error"]
    assert fr["journal_entry"]["last_error_code"] == "client_error"
    assert fr["journal_entry"]["attempt_count"] >= 1


async def test_review_maps_attempting_to_approved(ctx):
    """An 'attempting' entry (mid-push money-path marker) must read as 'approved' so
    the derived read model and the UI don't break on the new sync_status value."""
    book_id, ba = await _setup(ctx)
    tx = await _tx(ctx, book_id, ba, description="MIDPUSH", category="exp-office")
    je_id = (await ctx.client.post(f"/api/journal/transactions/{tx}/approve")).json()["id"]
    async with ctx.pool.connection() as conn:
        await conn.execute(
            "UPDATE journal_entries SET sync_status = 'attempting' WHERE id = %s", (je_id,)
        )

    row = _by_desc((await ctx.client.get("/api/review")).json()["transactions"], "MIDPUSH")
    assert row["state"] == "approved"
    assert row["journal_entry"]["sync_status"] == "attempting"


async def test_review_offers_active_targets_only(ctx):
    await _setup(ctx)
    body = (await ctx.client.get("/api/review")).json()
    ids = {a["qbo_id"] for group in body["targets"].values() for a in group}
    assert "exp-office" in ids       # active expense is offered
    assert "exp-old" not in ids      # inactive is not
    assert "cash-1" in ids           # active accounts, grouped by classification


async def test_approval_barred_for_uncategorized(ctx):
    book_id, ba = await _setup(ctx)
    tx = await _tx(ctx, book_id, ba, description="NO-CAT")  # no account assigned

    r = await ctx.client.post(f"/api/journal/transactions/{tx}/approve")
    assert r.status_code == 409  # Uncategorized -> blocked server-side

    # And the read model still shows it uncategorized (no JE materialized).
    rows = (await ctx.client.get("/api/review")).json()["transactions"]
    assert _by_desc(rows, "NO-CAT")["state"] == "uncategorized"
    assert _by_desc(rows, "NO-CAT")["journal_entry"] is None


async def test_review_requires_auth(ctx):
    # No session cookie set -> 401 (Bookkeeper-allowed, but must be authenticated).
    r = await ctx.client.get("/api/review")
    assert r.status_code == 401


async def test_qbo_fake_seed_makes_book_review_ready_and_is_idempotent(ctx):
    """The e2e affordance: QBO_FAKE seeding leaves a connected Book with a mirror,
    a bank account, a profile and a ready Bookkeeper — reachable without OAuth."""
    from app.features.review import e2e_fake

    async with ctx.pool.connection() as conn:
        await auth_service.bootstrap(conn)
    async with ctx.pool.connection() as conn:
        await e2e_fake._seed(conn, ctx.fake)
        await e2e_fake._seed(conn, ctx.fake)  # again: idempotent

    async with ctx.pool.connection() as conn:
        book_id = (await (await conn.execute("SELECT id FROM books LIMIT 1")).fetchone())[0]
        status = (await (await conn.execute(
            "SELECT status FROM quickbooks_connections WHERE book_id = %s", (book_id,)
        )).fetchone())[0]
        assert status == "connected"
        active = (await (await conn.execute(
            "SELECT count(*) FROM accounts WHERE book_id = %s AND active", (book_id,)
        )).fetchone())[0]
        assert active >= 2  # Checking + Office Supplies from the fake CoA
        banks = (await (await conn.execute(
            "SELECT count(*) FROM bank_accounts WHERE book_id = %s", (book_id,)
        )).fetchone())[0]
        profiles = (await (await conn.execute(
            "SELECT count(*) FROM column_mapping_profiles WHERE book_id = %s", (book_id,)
        )).fetchone())[0]
        assert banks == 1 and profiles == 1  # not duplicated by the second seed
        bk = await (await conn.execute(
            "SELECT role, must_set_password FROM users WHERE email = %s",
            (e2e_fake.E2E_BOOKKEEPER_EMAIL,),
        )).fetchone()
        assert bk == ("Bookkeeper", False)
