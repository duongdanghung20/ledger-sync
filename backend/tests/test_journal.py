"""Ticket 10: double-entry Journal Entry materialization — the money path.

Two layers:

* Pure sign rule (no DB) — build_lines / to_port_entry: balance by construction,
  outflow vs inflow sides, abs amounts, zero rejected, Decimal-not-float guard.
* HTTP + DB — approve materializes exactly one balanced entry; uncategorized / zero /
  unmapped-bank-account are each blocked with nothing persisted; the snapshot is
  immutable to re-categorization; requestid is stable and never regenerated;
  doc_number is monotonic per Book; un-approve discards a pending entry (and refuses a
  posted one); preview is reachable before approval; amounts stay 2dp Decimal.
"""

from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.db import get_conn
from app.features.auth import service as auth_service
from app.features.journal import build_lines, engine, to_port_entry
from app.main import app

# =========================================================================== #
#  Pure sign rule — no DB                                                      #
# =========================================================================== #


def _both(lines):
    return {l.posting_type: l for l in lines}


def test_build_lines_outflow_debits_category_credits_cash():
    debit, credit = build_lines(Decimal("-6.75"), "cat", "cash")
    assert (debit.posting_type, debit.account_id, debit.amount) == ("Debit", "cat", Decimal("6.75"))
    assert (credit.posting_type, credit.account_id, credit.amount) == ("Credit", "cash", Decimal("6.75"))
    assert debit.amount == credit.amount  # balanced by construction


def test_build_lines_inflow_debits_cash_credits_category():
    debit, credit = build_lines(Decimal("9000.00"), "cat", "cash")
    assert (debit.posting_type, debit.account_id) == ("Debit", "cash")
    assert (credit.posting_type, credit.account_id) == ("Credit", "cat")
    assert debit.amount == credit.amount == Decimal("9000.00")


def test_build_lines_rejects_zero():
    with pytest.raises(engine.ZeroAmount):
        build_lines(Decimal("0.00"), "cat", "cash")


def test_build_lines_rejects_float():
    """Money-path trust boundary: a float can never enter the sign rule."""
    with pytest.raises(TypeError):
        build_lines(-6.75, "cat", "cash")


@pytest.mark.parametrize("amount,debit_side,exp_debit", [
    (Decimal("6.75"), "category", "cat"),   # outflow snapshot -> Dr category
    (Decimal("9000.00"), "cash", "cash"),   # inflow snapshot  -> Dr cash
])
def test_to_port_entry_reconstructs_balanced_lines(amount, debit_side, exp_debit):
    je = {"id": "je-123", "amount": amount, "debit_side": debit_side,
          "category_account_qbo_id": "cat", "cash_account_qbo_id": "cash",
          "memo": "COFFEE", "doc_number": 7}
    entry = to_port_entry(je)
    by = _both(entry.lines)
    assert by["Debit"].account_id == exp_debit
    assert by["Debit"].amount == by["Credit"].amount == amount   # Dr == Cr, positive
    assert entry.doc_number == "7"
    assert "ledger-sync:je-123" in entry.private_note and "COFFEE" in entry.private_note


# =========================================================================== #
#  HTTP + DB                                                                   #
# =========================================================================== #


@pytest.fixture(autouse=True)
def _env(monkeypatch):
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


async def _book_as_bookkeeper(pool, client):
    async with pool.connection() as conn:
        await auth_service.bootstrap(conn)
    async with pool.connection() as conn:
        org_id = (await (await conn.execute("SELECT id FROM organizations LIMIT 1")).fetchone())[0]
        book_id = (await (await conn.execute(
            "SELECT id FROM books WHERE organization_id = %s LIMIT 1", (org_id,)
        )).fetchone())[0]
        user_id = (await (await conn.execute(
            "INSERT INTO users (organization_id, email, role, must_set_password) "
            "VALUES (%s, 'bk@example.com', 'Bookkeeper', false) RETURNING id", (org_id,)
        )).fetchone())[0]
        sid = await auth_service.create_session(conn, user_id)
    client.cookies.set("session", auth_service.sign_cookie(sid))
    return book_id


async def _account(pool, book_id, qbo_id, name, *, active=True, atype="Expense"):
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO accounts (book_id, qbo_id, name, account_type, classification, active) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (book_id, qbo_id, name, atype, atype, active),
        )


async def _bank_account(pool, book_id, cash_qbo_id):
    async with pool.connection() as conn:
        row = await (await conn.execute(
            "INSERT INTO bank_accounts (book_id, name, qbo_account_id) "
            "VALUES (%s, 'Checking', %s) RETURNING id", (book_id, cash_qbo_id)
        )).fetchone()
    return row[0]


async def _tx(pool, book_id, ba, *, amount, description="TXN", payee=None,
              category=None, d="2026-09-02"):
    async with pool.connection() as conn:
        row = await (await conn.execute(
            "INSERT INTO imported_transactions "
            "(book_id, bank_account_id, date, amount, description, payee, raw, dedup_key, "
            " assigned_account_qbo_id, category_source) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (book_id, ba, d, Decimal(amount), description, payee, Jsonb({}),
             description + amount, category, "manual" if category else "none"),
        )).fetchone()
    return str(row[0])


async def _je_row(pool, tx_id):
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT * FROM journal_entries WHERE imported_transaction_id = %s", (tx_id,)
            )
            return await cur.fetchone()


async def _standard_book(ctx, *, cash_active=True):
    """A book with an active category account + a cash account + a bank account
    mapped to that cash account. Returns (book_id, bank_account_id)."""
    book_id = await _book_as_bookkeeper(ctx.pool, ctx.client)
    await _account(ctx.pool, book_id, "cash-1", "Checking", atype="Bank", active=cash_active)
    await _account(ctx.pool, book_id, "exp-meals", "Meals")
    await _account(ctx.pool, book_id, "inc-sales", "Sales", atype="Income")
    ba = await _bank_account(ctx.pool, book_id, "cash-1")
    return book_id, ba


# --- balance & sign ---------------------------------------------------------


async def test_approve_outflow_materializes_balanced_entry_dr_category_cr_cash(ctx):
    book_id, ba = await _standard_book(ctx)
    tx = await _tx(ctx.pool, book_id, ba, amount="-6.75", description="STARBUCKS",
                   payee="Starbucks", category="exp-meals")

    r = await ctx.client.post(f"/api/journal/transactions/{tx}/approve")
    assert r.status_code == 201, r.text
    body = r.json()

    by = {l["posting_type"]: l for l in body["lines"]}
    assert by["Debit"]["account_id"] == "exp-meals"   # outflow debits category
    assert by["Credit"]["account_id"] == "cash-1"      # ...credits cash
    assert by["Debit"]["amount"] == by["Credit"]["amount"] == "6.75"   # Dr == Cr, abs

    je = await _je_row(ctx.pool, tx)
    assert je["debit_side"] == "category" and je["amount"] == Decimal("6.75")
    assert je["sync_status"] == "pending" and je["memo"] == "STARBUCKS — Starbucks"


async def test_approve_inflow_materializes_dr_cash_cr_category(ctx):
    book_id, ba = await _standard_book(ctx)
    tx = await _tx(ctx.pool, book_id, ba, amount="9000.00", description="STRIPE PAYOUT",
                   category="inc-sales")

    r = await ctx.client.post(f"/api/journal/transactions/{tx}/approve")
    assert r.status_code == 201, r.text
    by = {l["posting_type"]: l for l in r.json()["lines"]}
    assert by["Debit"]["account_id"] == "cash-1"       # inflow debits cash
    assert by["Credit"]["account_id"] == "inc-sales"   # ...credits category
    assert by["Debit"]["amount"] == by["Credit"]["amount"] == "9000.00"


async def test_approve_rejects_zero_amount_nothing_persisted(ctx):
    book_id, ba = await _standard_book(ctx)
    tx = await _tx(ctx.pool, book_id, ba, amount="0.00", description="ZERO", category="exp-meals")

    r = await ctx.client.post(f"/api/journal/transactions/{tx}/approve")
    assert r.status_code == 422
    assert await _je_row(ctx.pool, tx) is None


async def test_approve_barred_for_uncategorized(ctx):
    book_id, ba = await _standard_book(ctx)
    tx = await _tx(ctx.pool, book_id, ba, amount="-6.75", description="UNCLASSIFIED")  # no category

    r = await ctx.client.post(f"/api/journal/transactions/{tx}/approve")
    assert r.status_code == 409
    assert await _je_row(ctx.pool, tx) is None


async def test_approve_blocked_and_flagged_when_bank_account_unmapped(ctx):
    """Cash-side account inactive in the mirror -> unmapped for journal purposes:
    block + flag with a clear error, persist nothing (never post to a stale account)."""
    book_id, ba = await _standard_book(ctx, cash_active=False)
    tx = await _tx(ctx.pool, book_id, ba, amount="-6.75", description="X", category="exp-meals")

    r = await ctx.client.post(f"/api/journal/transactions/{tx}/approve")
    assert r.status_code == 409
    assert "active" in r.json()["detail"].lower()   # flagged with an explanatory reason
    assert await _je_row(ctx.pool, tx) is None


# --- immutability, requestid, doc_number ------------------------------------


async def test_snapshot_is_immutable_to_recategorization(ctx):
    book_id, ba = await _standard_book(ctx)
    await _account(ctx.pool, book_id, "exp-office", "Office")
    tx = await _tx(ctx.pool, book_id, ba, amount="-100.00", description="X", category="exp-meals")

    await ctx.client.post(f"/api/journal/transactions/{tx}/approve")
    before = await _je_row(ctx.pool, tx)

    # Re-categorize the transaction after approval (as a rule re-run / manual override would).
    async with ctx.pool.connection() as conn:
        await conn.execute(
            "UPDATE imported_transactions SET assigned_account_qbo_id = 'exp-office', "
            "amount = -999.00 WHERE id = %s", (tx,)
        )
    after = await _je_row(ctx.pool, tx)
    assert after["category_account_qbo_id"] == before["category_account_qbo_id"] == "exp-meals"
    assert after["amount"] == before["amount"] == Decimal("100.00")


async def test_requestid_minted_once_and_stable_across_reads(ctx):
    book_id, ba = await _standard_book(ctx)
    tx = await _tx(ctx.pool, book_id, ba, amount="-6.75", description="X", category="exp-meals")

    approved = (await ctx.client.post(f"/api/journal/transactions/{tx}/approve")).json()
    listed = (await ctx.client.get("/api/journal/entries")).json()["entries"][0]
    stored = await _je_row(ctx.pool, tx)

    assert approved["requestid"] == listed["requestid"] == str(stored["requestid"])


async def test_doc_number_monotonic_per_book(ctx):
    book_id, ba = await _standard_book(ctx)
    t1 = await _tx(ctx.pool, book_id, ba, amount="-10.00", description="A", category="exp-meals")
    t2 = await _tx(ctx.pool, book_id, ba, amount="-20.00", description="B", category="exp-meals")

    d1 = (await ctx.client.post(f"/api/journal/transactions/{t1}/approve")).json()["doc_number"]
    d2 = (await ctx.client.post(f"/api/journal/transactions/{t2}/approve")).json()["doc_number"]
    assert d1 == 1 and d2 == 2 and d2 > d1


async def test_reapprove_is_rejected(ctx):
    book_id, ba = await _standard_book(ctx)
    tx = await _tx(ctx.pool, book_id, ba, amount="-6.75", description="X", category="exp-meals")
    assert (await ctx.client.post(f"/api/journal/transactions/{tx}/approve")).status_code == 201
    assert (await ctx.client.post(f"/api/journal/transactions/{tx}/approve")).status_code == 409


# --- un-approve -------------------------------------------------------------


async def test_unapprove_discards_pending_back_to_categorized(ctx):
    book_id, ba = await _standard_book(ctx)
    tx = await _tx(ctx.pool, book_id, ba, amount="-6.75", description="X", category="exp-meals")
    je = (await ctx.client.post(f"/api/journal/transactions/{tx}/approve")).json()

    r = await ctx.client.post(f"/api/journal/entries/{je['id']}/unapprove")
    assert r.status_code == 200
    assert await _je_row(ctx.pool, tx) is None          # entry discarded

    # transaction is still categorized -> can be approved again
    assert (await ctx.client.post(f"/api/journal/transactions/{tx}/approve")).status_code == 201


async def test_unapprove_refuses_a_posted_entry(ctx):
    book_id, ba = await _standard_book(ctx)
    tx = await _tx(ctx.pool, book_id, ba, amount="-6.75", description="X", category="exp-meals")
    je = (await ctx.client.post(f"/api/journal/transactions/{tx}/approve")).json()
    async with ctx.pool.connection() as conn:
        await conn.execute("UPDATE journal_entries SET sync_status = 'posted' WHERE id = %s", (je["id"],))

    r = await ctx.client.post(f"/api/journal/entries/{je['id']}/unapprove")
    assert r.status_code == 409
    assert await _je_row(ctx.pool, tx) is not None       # still there


# --- preview & amounts ------------------------------------------------------


async def test_preview_before_approval_does_not_materialize(ctx):
    book_id, ba = await _standard_book(ctx)
    tx = await _tx(ctx.pool, book_id, ba, amount="-6.75", description="X", category="exp-meals")

    r = await ctx.client.get(f"/api/journal/transactions/{tx}/preview")
    assert r.status_code == 200
    body = r.json()
    assert body["balanced"] is True and body["debit_total"] == body["credit_total"] == "6.75"
    by = {l["posting_type"]: l for l in body["lines"]}
    assert by["Debit"]["account_id"] == "exp-meals" and by["Credit"]["account_id"] == "cash-1"
    assert await _je_row(ctx.pool, tx) is None           # preview persists nothing


async def test_preview_rejects_uncategorized(ctx):
    book_id, ba = await _standard_book(ctx)
    tx = await _tx(ctx.pool, book_id, ba, amount="-6.75", description="X")  # no category
    assert (await ctx.client.get(f"/api/journal/transactions/{tx}/preview")).status_code == 409


async def test_amount_stored_as_two_dp_decimal(ctx):
    book_id, ba = await _standard_book(ctx)
    tx = await _tx(ctx.pool, book_id, ba, amount="-6.75", description="X", category="exp-meals")
    await ctx.client.post(f"/api/journal/transactions/{tx}/approve")
    je = await _je_row(ctx.pool, tx)
    assert isinstance(je["amount"], Decimal)
    assert je["amount"] == Decimal("6.75")
    assert je["amount"].as_tuple().exponent == -2      # exactly 2 decimal places


# --- auth -------------------------------------------------------------------


async def test_routes_require_authentication(ctx):
    assert (await ctx.client.get("/api/journal/entries")).status_code == 401
    assert (await ctx.client.post("/api/journal/transactions/"
                                  "00000000-0000-0000-0000-000000000000/approve")).status_code == 401
