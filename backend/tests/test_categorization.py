"""Ticket 09: categorization engine, rules, manual override.

Two layers:

* Pure engine (no DB) — the matcher lifted from the ticket-04 prototype, exercised
  table-driven across the combinatorial cases: text/numeric/date operators, AND within
  a rule, first-match-wins priority, signed-amount matching, invalid-target skip, and
  manual override beating a rule.
* HTTP + DB — rules CRUD, run applying rules, manual override beating a rule AND
  surviving a re-run, category_source + matched_rule_id recorded, and a rule whose
  target went inactive being flagged invalid and skipped so the txn stays uncategorized.
"""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from psycopg.types.json import Jsonb

from app.db import get_conn
from app.features.auth import service as auth_service
from app.features.categorization import categorize, categorize_one, match_condition
from app.features.categorization import engine as E
from app.main import app

# --------------------------------------------------------------------------- #
#  Prototype corpus (from .scratch/v1-spec/issues/04-categorization-prototype   #
#  .html — the validated source), as canonical rows: Decimal amount, date obj.  #
# --------------------------------------------------------------------------- #


def _txn(id, payee, description, amount, d="2026-09-02"):
    return {"id": id, "payee": payee, "description": description,
            "amount": Decimal(amount), "date": date.fromisoformat(d)}


TXNS = [
    _txn("t1", "Starbucks", "STARBUCKS #123 SEATTLE WA", "-6.75", "2026-09-02"),
    _txn("t3", "AMZN Mktp", "AMZN MKTP US*2X4 AMZN.COM/BILL", "-142.30", "2026-09-04"),
    _txn("t4", "AMZN Mktp", "AMZN MKTP US*9K1", "-18.99", "2026-09-05"),
    _txn("t6", "Acme Corp", "ACME CORP PAYMENT ACH", "2500.00", "2026-09-07"),
    _txn("t7", "Starbucks", "SBUX ONLINE ORDER", "-12.40", "2026-09-08"),
]


def _rule(id, priority, target, conditions, invalid=False):
    return {"id": id, "priority": priority, "target_qbo_account_id": target,
            "conditions": conditions, "invalid_target": invalid}


# =========================================================================== #
#  Pure engine — table-driven                                                  #
# =========================================================================== #


@pytest.mark.parametrize(
    "field,operator,value,expected",
    [
        ("payee", "contains", "starbucks", True),      # case-insensitive
        ("payee", "contains", "SHELL", False),
        ("description", "contains", "seattle", True),
        ("payee", "equals", "starbucks", True),        # case-insensitive equals
        ("payee", "equals", "starbuck", False),        # equals is not contains
    ],
)
def test_text_operators_case_insensitive(field, operator, value, expected):
    t = TXNS[0]  # Starbucks / STARBUCKS #123 SEATTLE WA
    assert match_condition(t, {"field": field, "operator": operator, "value": value}) is expected


@pytest.mark.parametrize(
    "amount,operator,value,value2,expected",
    [
        ("-142.30", "lte", -100, None, True),    # -142.30 <= -100  (a $100+ purchase)
        ("-18.99", "lte", -100, None, False),    # -18.99 is not <= -100
        ("2500.00", "gte", 1000, None, True),    # a $1000+ deposit
        ("-6.75", "gte", 1000, None, False),
        ("-52.10", "between", -100, -10, True),  # signed range
        ("-142.30", "between", -100, -10, False),
    ],
)
def test_amount_operators_on_signed_amount(amount, operator, value, value2, expected):
    cond = {"field": "amount", "operator": operator, "value": value}
    if value2 is not None:
        cond["value2"] = value2
    assert match_condition(_txn("x", "p", "d", amount), cond) is expected


@pytest.mark.parametrize(
    "operator,value,value2,expected",
    [
        ("gte", "2026-09-01", None, True),
        ("gte", "2026-10-01", None, False),
        ("lte", "2026-09-30", None, True),
        ("between", "2026-09-01", "2026-09-30", True),
        ("between", "2026-01-01", "2026-01-31", False),
    ],
)
def test_date_operators(operator, value, value2, expected):
    cond = {"field": "date", "operator": operator, "value": value}
    if value2 is not None:
        cond["value2"] = value2
    assert match_condition(_txn("x", "p", "d", "-1.00", "2026-09-15"), cond) is expected


def test_and_within_a_rule():
    """Large Amazon → Office needs description contains 'amzn' AND amount lte -100.
    t3 (-142.30) matches both; t4 (-18.99) matches the text but fails the amount."""
    rule = _rule("r", 10, "exp-office", [
        {"field": "description", "operator": "contains", "value": "amzn"},
        {"field": "amount", "operator": "lte", "value": -100},
    ])
    assert categorize_one(TXNS[1], [rule])["account_id"] == "exp-office"   # t3
    assert categorize_one(TXNS[2], [rule])["source"] == "none"            # t4


def test_first_match_wins_by_priority_then_reorder():
    """t7 (SBUX, payee Starbucks) matches both rules; lowest priority wins → office.
    Swap priorities → meals. Silent priority tiebreak, no conflict flagged."""
    coffee = _rule("r0", 5, "exp-office", [{"field": "description", "operator": "contains", "value": "sbux"}])
    meals = _rule("r1", 10, "exp-meals", [{"field": "payee", "operator": "contains", "value": "starbucks"}])
    assert categorize_one(TXNS[4], [meals, coffee])["account_id"] == "exp-office"

    coffee["priority"], meals["priority"] = 10, 5
    assert categorize_one(TXNS[4], [meals, coffee])["account_id"] == "exp-meals"


def test_signed_amount_hundred_plus_outflow():
    """"purchases of $100+" = amount lte -100: a big outflow matches, small doesn't,
    an inflow of the same magnitude doesn't."""
    rule = _rule("r", 10, "exp", [{"field": "amount", "operator": "lte", "value": -100}])
    assert categorize_one(_txn("a", "p", "d", "-142.30"), [rule])["source"] == "rule"
    assert categorize_one(_txn("b", "p", "d", "-18.99"), [rule])["source"] == "none"
    assert categorize_one(_txn("c", "p", "d", "2500.00"), [rule])["source"] == "none"


def test_invalid_target_rule_is_skipped():
    """A matching rule flagged invalid_target is skipped; the txn falls through."""
    dead = _rule("r0", 5, "gone", [{"field": "payee", "operator": "contains", "value": "starbucks"}], invalid=True)
    live = _rule("r1", 10, "exp-meals", [{"field": "payee", "operator": "contains", "value": "starbucks"}])
    assert categorize_one(TXNS[0], [dead])["source"] == "none"
    assert categorize_one(TXNS[0], [dead, live])["account_id"] == "exp-meals"


def test_categorize_manual_override_beats_rule():
    rule = _rule("r", 10, "exp-meals", [{"field": "payee", "operator": "contains", "value": "starbucks"}])
    results = {r["txn"]["id"]: r for r in categorize(TXNS, [rule], {"t1": "exp-office"})}
    assert results["t1"] == {"txn": TXNS[0], "account_id": "exp-office", "source": "manual", "rule_id": None}
    assert results["t7"]["source"] == "rule"  # not overridden -> rule still applies


@pytest.mark.parametrize("conditions", [
    [],                                                                   # empty
    "notalist",                                                           # not a list
    [{"field": "bogus", "operator": "contains", "value": "x"}],           # unknown field
    [{"field": "amount", "operator": "contains", "value": "x"}],          # op wrong for field
    [{"field": "payee", "operator": "gte", "value": "x"}],                # op wrong for field
    [{"field": "payee", "operator": "contains", "value": ""}],            # missing value
    [{"field": "amount", "operator": "between", "value": -100}],          # between needs value2
])
def test_validate_conditions_rejects_broken(conditions):
    with pytest.raises(E.RuleError):
        E.validate_conditions(conditions)


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


async def _account(pool, book_id, qbo_id, name, *, active=True):
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO accounts (book_id, qbo_id, name, account_type, classification, active) "
            "VALUES (%s, %s, %s, 'Expense', 'Expense', %s)",
            (book_id, qbo_id, name, active),
        )


async def _bank_account(pool, book_id):
    async with pool.connection() as conn:
        row = await (await conn.execute(
            "INSERT INTO bank_accounts (book_id, name, qbo_account_id) "
            "VALUES (%s, 'Checking', '1') RETURNING id", (book_id,)
        )).fetchone()
    return row[0]


async def _tx(pool, book_id, ba, *, amount, description, payee=None, d="2026-09-02"):
    async with pool.connection() as conn:
        row = await (await conn.execute(
            "INSERT INTO imported_transactions "
            "(book_id, bank_account_id, date, amount, description, payee, raw, dedup_key) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (book_id, ba, d, Decimal(amount), description, payee, Jsonb({}), description + amount),
        )).fetchone()
    return str(row[0])


async def _tx_row(pool, book_id, tx_id):
    async with pool.connection() as conn:
        from psycopg.rows import dict_row
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT assigned_account_qbo_id, category_source, matched_rule_id "
                "FROM imported_transactions WHERE id = %s", (tx_id,)
            )
            return await cur.fetchone()


async def _make_rule(client, priority, target, conditions):
    r = await client.post("/api/categorization/rules", json={
        "priority": priority, "target_qbo_account_id": target, "conditions": conditions,
    })
    assert r.status_code == 201, r.text
    return r.json()["rules"]


# --- rules CRUD + run -------------------------------------------------------


async def test_run_applies_rule_records_source_and_matched_rule(ctx):
    book_id = await _book_as_bookkeeper(ctx.pool, ctx.client)
    await _account(ctx.pool, book_id, "exp-meals", "Meals")
    ba = await _bank_account(ctx.pool, book_id)
    tx = await _tx(ctx.pool, book_id, ba, amount="-6.75", description="STARBUCKS #123", payee="Starbucks")

    rules = await _make_rule(ctx.client, 10, "exp-meals",
                             [{"field": "payee", "operator": "contains", "value": "starbucks"}])
    rule_id = rules[0]["id"]

    summary = (await ctx.client.post("/api/categorization/run")).json()
    assert summary == {"total": 1, "by_rule": 1, "manual": 0, "uncategorized": 0}

    row = await _tx_row(ctx.pool, book_id, tx)
    assert row["category_source"] == "rule"
    assert row["assigned_account_qbo_id"] == "exp-meals"
    assert str(row["matched_rule_id"]) == rule_id


async def test_manual_override_beats_rule_and_survives_rerun(ctx):
    book_id = await _book_as_bookkeeper(ctx.pool, ctx.client)
    await _account(ctx.pool, book_id, "exp-meals", "Meals")
    await _account(ctx.pool, book_id, "exp-office", "Office")
    ba = await _bank_account(ctx.pool, book_id)
    tx = await _tx(ctx.pool, book_id, ba, amount="-6.75", description="STARBUCKS #123", payee="Starbucks")
    await _make_rule(ctx.client, 10, "exp-meals",
                     [{"field": "payee", "operator": "contains", "value": "starbucks"}])

    await ctx.client.post("/api/categorization/run")           # -> rule -> exp-meals
    # bookkeeper disagrees, overrides to Office
    r = await ctx.client.post(f"/api/categorization/transactions/{tx}/assign",
                              json={"qbo_account_id": "exp-office"})
    assert r.status_code == 200 and r.json()["category_source"] == "manual"

    summary = (await ctx.client.post("/api/categorization/run")).json()  # re-run must not clobber
    assert summary["manual"] == 1
    row = await _tx_row(ctx.pool, book_id, tx)
    assert row["category_source"] == "manual" and row["assigned_account_qbo_id"] == "exp-office"


async def test_rule_with_inactive_target_flagged_invalid_and_skipped(ctx):
    book_id = await _book_as_bookkeeper(ctx.pool, ctx.client)
    await _account(ctx.pool, book_id, "exp-meals", "Meals")     # active when rule is made
    ba = await _bank_account(ctx.pool, book_id)
    tx = await _tx(ctx.pool, book_id, ba, amount="-6.75", description="STARBUCKS", payee="Starbucks")
    rules = await _make_rule(ctx.client, 10, "exp-meals",
                             [{"field": "payee", "operator": "contains", "value": "starbucks"}])
    assert rules[0]["invalid_target"] is False

    # the target account goes inactive in QBO's mirror
    async with ctx.pool.connection() as conn:
        await conn.execute("UPDATE accounts SET active = false WHERE qbo_id = 'exp-meals'")

    summary = (await ctx.client.post("/api/categorization/run")).json()
    assert summary == {"total": 1, "by_rule": 0, "manual": 0, "uncategorized": 1}
    row = await _tx_row(ctx.pool, book_id, tx)
    assert row["category_source"] == "none"  # skipped -> falls through to manual-eligible

    view = (await ctx.client.get("/api/categorization/rules")).json()
    assert view["rules"][0]["invalid_target"] is True  # kept, re-flagged on run

    # and a bookkeeper can still hand-assign it once a valid account exists
    await _account(ctx.pool, book_id, "exp-office", "Office")
    r = await ctx.client.post(f"/api/categorization/transactions/{tx}/assign",
                              json={"qbo_account_id": "exp-office"})
    assert r.status_code == 200


async def test_assign_rejects_target_that_is_not_an_active_account(ctx):
    book_id = await _book_as_bookkeeper(ctx.pool, ctx.client)
    ba = await _bank_account(ctx.pool, book_id)
    tx = await _tx(ctx.pool, book_id, ba, amount="-6.75", description="X")
    r = await ctx.client.post(f"/api/categorization/transactions/{tx}/assign",
                              json={"qbo_account_id": "not-a-real-account"})
    assert r.status_code == 422


async def test_clear_resets_to_uncategorized(ctx):
    book_id = await _book_as_bookkeeper(ctx.pool, ctx.client)
    await _account(ctx.pool, book_id, "exp-meals", "Meals")
    ba = await _bank_account(ctx.pool, book_id)
    tx = await _tx(ctx.pool, book_id, ba, amount="-6.75", description="X")
    await ctx.client.post(f"/api/categorization/transactions/{tx}/assign",
                          json={"qbo_account_id": "exp-meals"})
    r = await ctx.client.delete(f"/api/categorization/transactions/{tx}/category")
    assert r.status_code == 200 and r.json()["category_source"] == "none"
    assert (await _tx_row(ctx.pool, book_id, tx))["assigned_account_qbo_id"] is None


async def test_rule_crud_and_validation(ctx):
    book_id = await _book_as_bookkeeper(ctx.pool, ctx.client)
    await _account(ctx.pool, book_id, "exp-meals", "Meals")
    rules = await _make_rule(ctx.client, 10, "exp-meals",
                             [{"field": "payee", "operator": "contains", "value": "starbucks"}])
    rid = rules[0]["id"]

    # a broken condition is 422
    bad = await ctx.client.post("/api/categorization/rules", json={
        "priority": 20, "target_qbo_account_id": "exp-meals",
        "conditions": [{"field": "amount", "operator": "contains", "value": "x"}],
    })
    assert bad.status_code == 422

    # patch priority
    r = await ctx.client.patch(f"/api/categorization/rules/{rid}", json={"priority": 99})
    assert r.status_code == 200
    assert next(x for x in r.json()["rules"] if x["id"] == rid)["priority"] == 99

    # delete
    assert (await ctx.client.delete(f"/api/categorization/rules/{rid}")).status_code == 204
    assert (await ctx.client.delete(f"/api/categorization/rules/{rid}")).status_code == 404


async def test_routes_require_authentication(ctx):
    assert (await ctx.client.get("/api/categorization/rules")).status_code == 401
    assert (await ctx.client.post("/api/categorization/run")).status_code == 401
