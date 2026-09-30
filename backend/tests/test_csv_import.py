"""Ticket 08: CSV import, Column-Mapping Profiles, per-Bank-Account dedup.

Two layers:

* Pure engine (no DB) — the parse + multiset dedup logic lifted from the ticket-05
  prototype, exercised table-driven against its dedup corpus (the samples + the
  crux scenarios that pass 7/7 in the prototype).
* HTTP + DB money-path — the strict-correctness bar: overlapping re-import never
  double-imports; two genuinely identical same-day transactions both persist; the
  same row in two Bank Accounts stays two rows; external_id dedups with certainty;
  malformed rows are rejected with reasons while valid rows import; possible
  in-file duplicates surface as a count; amounts store as 2dp Decimal.
"""

from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

from app.db import get_conn
from app.features.auth import service as auth_service
from app.features.csv_import import engine as E
from app.features.csv_import import list_imported_transactions
from app.main import app

# --------------------------------------------------------------------------- #
#  Prototype corpus (verbatim from .scratch/v1-spec/issues/05-csv-import-        #
#  prototype.html — the authoritative validated source).                        #
# --------------------------------------------------------------------------- #

CHASE_CSV = (
    "Details,Posting Date,Description,Amount,Type,Balance\n"
    'DEBIT,09/01/2026,"STARBUCKS STORE 123",-5.00,DEBIT_CARD,1000.00\n'
    'DEBIT,09/01/2026,"STARBUCKS STORE 123",-5.00,DEBIT_CARD,995.00\n'
    'CREDIT,09/02/2026,"PAYROLL DIRECT DEP",2500.00,ACH,3495.00\n'
    'DEBIT,09/03/2026,"AMAZON MKTPL, ORDER 111",-42.13,DEBIT_CARD,3452.87\n'
    "DEBIT,09/04/2026,,-9.99,DEBIT_CARD,3442.88"
)
CHASE_CFG = {
    "delimiter": ",", "decimal": "dot", "header_row": 0,
    "date": {"column": "Posting Date", "format": "MM/DD/YYYY"},
    "description": {"column": "Description"},
    "amount": {"mode": "signed", "column": "Amount", "flip": False},
}

AMEX_CSV = (
    "Date,Description,Debit,Credit,Reference\n"
    "09/01/2026,UBER TRIP,12.50,,\n"
    "09/02/2026,REFUND UBER,,12.50,\n"
    "09/05/2026,WHOLE FOODS,88.20,,"
)
AMEX_CFG = {
    "delimiter": ",", "decimal": "dot",
    "date": {"column": "Date", "format": "MM/DD/YYYY"},
    "description": {"column": "Description"},
    "amount": {"mode": "debit_credit", "debit_column": "Debit", "credit_column": "Credit"},
}

EU_CSV = (
    "Booking Date;Counterparty;Purpose;Amount;Transaction ID\n"
    "2026-09-01;Lidl;Groceries;-23,45;TX-001\n"
    "2026-09-01;Lidl;Groceries;-23,45;TX-002\n"
    "2026-09-03;Employer;Salary;3.200,00;TX-050"
)
EU_CFG = {
    "delimiter": ";", "decimal": "comma",
    "date": {"column": "Booking Date", "format": "YYYY-MM-DD"},
    "description": {"column": "Purpose"}, "payee": {"column": "Counterparty"},
    "external_id": {"column": "Transaction ID"},
    "amount": {"mode": "signed", "column": "Amount", "flip": False},
}


# =========================================================================== #
#  Pure engine — table-driven                                                  #
# =========================================================================== #


@pytest.mark.parametrize(
    "raw,style,expected",
    [
        ("-5.00", "dot", Decimal("-5.00")),
        ("1,234.56", "dot", Decimal("1234.56")),
        ("2500.00", "dot", Decimal("2500.00")),
        ("-23,45", "comma", Decimal("-23.45")),
        ("3.200,00", "comma", Decimal("3200.00")),
        ("$1,000.00", "dot", Decimal("1000.00")),
        ("(42.13)", "dot", Decimal("-42.13")),   # parentheses negative
        ("", "dot", None),
        ("   ", "dot", None),
        ("abc", "dot", None),
    ],
)
def test_parse_amount_both_decimal_styles(raw, style, expected):
    got = E.parse_amount(raw, style)
    assert got == expected
    if got is not None:
        assert got.as_tuple().exponent == -2  # always 2dp


@pytest.mark.parametrize(
    "raw,fmt,ok",
    [
        ("09/01/2026", "MM/DD/YYYY", True),
        ("2026-09-01", "YYYY-MM-DD", True),
        ("01/09/2026", "DD/MM/YYYY", True),
        ("2026-09-01T00:00:00", "YYYY-MM-DD", True),  # trailing time tolerated
        ("13/40/2026", "MM/DD/YYYY", False),          # invalid month/day
        ("not-a-date", "YYYY-MM-DD", False),
        ("", "YYYY-MM-DD", False),
    ],
)
def test_parse_date_by_format(raw, fmt, ok):
    assert (E.parse_date(raw, fmt) is not None) == ok


def test_map_rows_chase_signed_dot_with_rejects():
    m = E.map_rows(CHASE_CSV, CHASE_CFG)
    assert len(m.good) == 4  # two coffees + payroll + amazon
    assert len(m.bad) == 1 and m.bad[0].reasons == ["empty description"]  # nothing silently dropped
    amounts = sorted(g.amount for g in m.good)
    assert amounts == [Decimal("-42.13"), Decimal("-5.00"), Decimal("-5.00"), Decimal("2500.00")]


def test_map_rows_amex_debit_credit_signs():
    m = E.map_rows(AMEX_CSV, AMEX_CFG)
    by = {g.description: g.amount for g in m.good}
    assert by == {
        "UBER TRIP": Decimal("-12.50"),    # debit = money out
        "REFUND UBER": Decimal("12.50"),   # credit = money in
        "WHOLE FOODS": Decimal("-88.20"),
    }


def test_map_rows_eu_comma_decimal_and_semicolon():
    m = E.map_rows(EU_CSV, EU_CFG)
    assert len(m.good) == 3
    salary = next(g for g in m.good if g.external_id == "TX-050")
    assert salary.amount == Decimal("3200.00") and salary.payee == "Employer"


def test_map_rows_flip_inverts_signed_amount():
    cfg = {**CHASE_CFG, "amount": {"mode": "signed", "column": "Amount", "flip": True}}
    m = E.map_rows(CHASE_CSV, cfg)
    payroll = next(g for g in m.good if "PAYROLL" in g.description)
    assert payroll.amount == Decimal("-2500.00")


def test_reconcile_content_multiset_keeps_identicals_skips_reexport():
    """The dedup crux: first import keeps both coffees; re-import skips both; a
    later file with a 3rd genuine coffee adds exactly one."""
    m = E.map_rows(CHASE_CSV, CHASE_CFG)
    first = E.reconcile(m.good, [])
    assert len(first.to_import) == 4 and first.in_file_duplicate_count == 1  # 2 coffees flagged
    existing = [E.dedup_key(r) for r in first.to_import]

    again = E.reconcile(m.good, existing)
    assert len(again.to_import) == 0 and len(again.duplicates) == 4  # never double-imports

    coffee = next(g for g in m.good if "STARBUCKS" in g.description)
    third_file = E.reconcile([coffee, coffee, coffee], existing)  # 3 coffees vs 2 existing
    assert len(third_file.to_import) == 1 and len(third_file.duplicates) == 2


def test_reconcile_external_id_is_certain():
    m = E.map_rows(EU_CSV, EU_CFG)
    first = E.reconcile(m.good, [])
    # two Lidl rows are content-identical but have distinct ids -> both kept, and
    # external ids are certain so they are NOT counted as possible in-file dups.
    assert len(first.to_import) == 3 and first.in_file_duplicate_count == 0
    existing = [E.dedup_key(r) for r in first.to_import]
    again = E.reconcile(m.good, existing)
    assert len(again.to_import) == 0 and len(again.duplicates) == 3


def test_reconcile_repeated_external_id_in_one_file_dedups():
    row = E.ParsedRow(
        date=E.parse_date("2026-09-01", "YYYY-MM-DD"), amount=Decimal("-1.00"),
        description="x", payee="", external_id="TX-DUP",
    )
    r = E.reconcile([row, row], [])
    assert len(r.to_import) == 1 and len(r.duplicates) == 1  # certain id can't repeat


def test_validate_config_rejects_broken_profiles():
    for bad in [
        {},
        {"date": {"column": "D"}, "description": {"column": "X"}, "amount": {"mode": "signed"}},
        {"date": {"column": "D", "format": "MM/DD/YYYY"}, "description": {"column": "X"},
         "amount": {"mode": "nonsense"}},
        {"date": {"column": "D", "format": "BAD"}, "description": {"column": "X"},
         "amount": {"mode": "signed", "column": "A"}},
    ]:
        with pytest.raises(E.ConfigError):
            E.validate_config(bad)


# =========================================================================== #
#  HTTP + DB money-path                                                        #
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
    """Bootstrap, log a Bookkeeper in (import is Bookkeeper-allowed). Returns book_id."""
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


async def _bank_account(pool, book_id, name):
    async with pool.connection() as conn:
        row = await (await conn.execute(
            "INSERT INTO bank_accounts (book_id, name, qbo_account_id) "
            "VALUES (%s, %s, '1') RETURNING id", (book_id, name)
        )).fetchone()
    return str(row[0])


async def _make_profile(client, name, config, bank_account_id=None):
    r = await client.post(
        "/api/csv-import/profiles",
        json={"name": name, "config": config, "bank_account_id": bank_account_id},
    )
    assert r.status_code == 201, r.text
    return next(p["id"] for p in r.json()["profiles"] if p["name"] == name)


async def _import(client, csv, profile_id, bank_account_id):
    r = await client.post(
        "/api/csv-import",
        json={"csv": csv, "profile_id": profile_id, "bank_account_id": bank_account_id},
    )
    assert r.status_code == 200, r.text
    return r.json()


# --- money-path -------------------------------------------------------------


async def test_money_overlapping_reimport_never_double_imports(ctx):
    book_id = await _book_as_bookkeeper(ctx.pool, ctx.client)
    ba = await _bank_account(ctx.pool, book_id, "Chase Checking")
    pid = await _make_profile(ctx.client, "Chase", CHASE_CFG, ba)

    first = await _import(ctx.client, CHASE_CSV, pid, ba)
    assert first["imported"] == 4 and first["duplicates"] == 0

    again = await _import(ctx.client, CHASE_CSV, pid, ba)
    assert again["imported"] == 0 and again["duplicates"] == 4  # every row a known re-export

    async with ctx.pool.connection() as conn:
        rows = await list_imported_transactions(conn, book_id, bank_account_id=ba)
    assert len(rows) == 4  # not 8 — no double-import


async def test_money_two_identical_same_day_both_kept(ctx):
    book_id = await _book_as_bookkeeper(ctx.pool, ctx.client)
    ba = await _bank_account(ctx.pool, book_id, "Chase Checking")
    pid = await _make_profile(ctx.client, "Chase", CHASE_CFG, ba)

    await _import(ctx.client, CHASE_CSV, pid, ba)
    async with ctx.pool.connection() as conn:
        rows = await list_imported_transactions(conn, book_id, bank_account_id=ba)
    coffees = [r for r in rows if r["description"] == "STARBUCKS STORE 123"]
    assert len(coffees) == 2  # two genuine identical same-day transactions both kept


async def test_money_same_row_in_two_bank_accounts_stays_distinct(ctx):
    book_id = await _book_as_bookkeeper(ctx.pool, ctx.client)
    ba1 = await _bank_account(ctx.pool, book_id, "Checking")
    ba2 = await _bank_account(ctx.pool, book_id, "Card")
    pid = await _make_profile(ctx.client, "Chase", CHASE_CFG)

    one_row = "Details,Posting Date,Description,Amount,Type,Balance\nDEBIT,09/01/2026,COFFEE,-5.00,X,0"
    r1 = await _import(ctx.client, one_row, pid, ba1)
    r2 = await _import(ctx.client, one_row, pid, ba2)  # same content, different account
    assert r1["imported"] == 1 and r2["imported"] == 1 and r2["duplicates"] == 0

    async with ctx.pool.connection() as conn:
        assert len(await list_imported_transactions(conn, book_id, bank_account_id=ba1)) == 1
        assert len(await list_imported_transactions(conn, book_id, bank_account_id=ba2)) == 1
        assert len(await list_imported_transactions(conn, book_id)) == 2  # two distinct rows


async def test_money_external_id_dedups_with_certainty(ctx):
    book_id = await _book_as_bookkeeper(ctx.pool, ctx.client)
    ba = await _bank_account(ctx.pool, book_id, "EU Giro")
    pid = await _make_profile(ctx.client, "EU", EU_CFG, ba)

    first = await _import(ctx.client, EU_CSV, pid, ba)
    assert first["imported"] == 3  # both Lidl rows kept via distinct ids
    again = await _import(ctx.client, EU_CSV, pid, ba)
    assert again["imported"] == 0 and again["duplicates"] == 3  # certain re-export skip

    async with ctx.pool.connection() as conn:
        rows = await list_imported_transactions(conn, book_id, bank_account_id=ba)
    assert len(rows) == 3
    lidl = [r for r in rows if r["description"] == "Groceries"]
    assert len(lidl) == 2 and {r["external_id"] for r in lidl} == {"TX-001", "TX-002"}


async def test_money_malformed_rows_rejected_with_reasons_valid_still_import(ctx):
    book_id = await _book_as_bookkeeper(ctx.pool, ctx.client)
    ba = await _bank_account(ctx.pool, book_id, "Chase")
    pid = await _make_profile(ctx.client, "Chase", CHASE_CFG, ba)

    res = await _import(ctx.client, CHASE_CSV, pid, ba)
    assert res["imported"] == 4  # valid rows still import
    assert len(res["rejected"]) == 1
    assert res["rejected"][0]["reasons"] == ["empty description"]  # nothing silently dropped

    async with ctx.pool.connection() as conn:
        assert len(await list_imported_transactions(conn, book_id, bank_account_id=ba)) == 4


async def test_money_in_file_duplicate_count_surfaced(ctx):
    book_id = await _book_as_bookkeeper(ctx.pool, ctx.client)
    ba = await _bank_account(ctx.pool, book_id, "Chase")
    pid = await _make_profile(ctx.client, "Chase", CHASE_CFG, ba)

    res = await _import(ctx.client, CHASE_CSV, pid, ba)
    # the two identical coffees are surfaced as a possible-duplicate count, not decided
    assert res["in_file_duplicate_count"] == 1
    async with ctx.pool.connection() as conn:
        assert len(await list_imported_transactions(conn, book_id, bank_account_id=ba)) == 4


async def test_amounts_stored_as_two_dp_decimal(ctx):
    book_id = await _book_as_bookkeeper(ctx.pool, ctx.client)
    ba = await _bank_account(ctx.pool, book_id, "EU Giro")
    pid = await _make_profile(ctx.client, "EU", EU_CFG, ba)
    await _import(ctx.client, EU_CSV, pid, ba)

    async with ctx.pool.connection() as conn:
        rows = await list_imported_transactions(conn, book_id, bank_account_id=ba)
    for r in rows:
        assert isinstance(r["amount"], Decimal)          # never float
        assert r["amount"].as_tuple().exponent == -2     # exactly 2dp
    salary = next(r for r in rows if r["external_id"] == "TX-050")
    assert salary["amount"] == Decimal("3200.00")        # comma-decimal parsed


# --- profiles / suggestion / auth ------------------------------------------


async def test_profile_persists_and_autosuggested_from_header_signature(ctx):
    book_id = await _book_as_bookkeeper(ctx.pool, ctx.client)
    ba = await _bank_account(ctx.pool, book_id, "Chase")
    chase_pid = await _make_profile(ctx.client, "Chase", CHASE_CFG, ba)
    await _make_profile(ctx.client, "EU", EU_CFG, ba)  # a second profile to choose against

    r = await ctx.client.post("/api/csv-import/preview", json={"csv": CHASE_CSV})
    assert r.status_code == 200
    body = r.json()
    assert body["suggested_profile_id"] == chase_pid            # matched by header signature
    assert "Posting Date" in body["header"]
    assert len(body["profiles"]) == 2 and len(body["bank_accounts"]) == 1


async def test_bank_account_chosen_at_import_every_tx_carries_it(ctx):
    book_id = await _book_as_bookkeeper(ctx.pool, ctx.client)
    ba = await _bank_account(ctx.pool, book_id, "Chase")
    pid = await _make_profile(ctx.client, "Chase", CHASE_CFG)  # no default -> chosen at import
    await _import(ctx.client, CHASE_CSV, pid, ba)

    async with ctx.pool.connection() as conn:
        rows = await list_imported_transactions(conn, book_id)
    assert rows and all(str(r["bank_account_id"]) == ba for r in rows)


async def test_import_rejects_bank_account_from_another_book(ctx):
    await _book_as_bookkeeper(ctx.pool, ctx.client)
    r = await ctx.client.post(
        "/api/csv-import",
        json={
            "csv": CHASE_CSV,
            "profile_id": "00000000-0000-0000-0000-000000000000",
            "bank_account_id": "00000000-0000-0000-0000-000000000000",
        },
    )
    assert r.status_code == 404  # profile not found (guards before touching the file)


async def test_profile_crud(ctx):
    book_id = await _book_as_bookkeeper(ctx.pool, ctx.client)
    ba = await _bank_account(ctx.pool, book_id, "Chase")
    pid = await _make_profile(ctx.client, "Chase", CHASE_CFG, ba)

    # update: rename + clear default bank account
    r = await ctx.client.patch(
        f"/api/csv-import/profiles/{pid}", json={"name": "Chase Renamed", "bank_account_id": None}
    )
    assert r.status_code == 200
    prof = next(p for p in r.json()["profiles"] if p["id"] == pid)
    assert prof["name"] == "Chase Renamed" and prof["bank_account_id"] is None

    # a broken config update is rejected
    assert (await ctx.client.patch(
        f"/api/csv-import/profiles/{pid}", json={"config": {"amount": {"mode": "x"}}}
    )).status_code == 422

    # delete
    assert (await ctx.client.delete(f"/api/csv-import/profiles/{pid}")).status_code == 204
    assert (await ctx.client.delete(f"/api/csv-import/profiles/{pid}")).status_code == 404


async def test_creating_profile_with_invalid_config_is_422(ctx):
    await _book_as_bookkeeper(ctx.pool, ctx.client)
    r = await ctx.client.post(
        "/api/csv-import/profiles",
        json={"name": "Broken", "config": {"date": {"column": "D"}}},
    )
    assert r.status_code == 422


async def test_routes_require_authentication(ctx):
    assert (await ctx.client.get("/api/csv-import/profiles")).status_code == 401
    assert (await ctx.client.post("/api/csv-import/preview", json={"csv": "x"})).status_code == 401


async def test_list_imported_transactions_seam_exposes_categorization_columns(ctx):
    book_id = await _book_as_bookkeeper(ctx.pool, ctx.client)
    ba = await _bank_account(ctx.pool, book_id, "Chase")
    pid = await _make_profile(ctx.client, "Chase", CHASE_CFG, ba)
    await _import(ctx.client, CHASE_CSV, pid, ba)

    async with ctx.pool.connection() as conn:
        rows = await list_imported_transactions(conn, book_id)
    r = rows[0]
    # the columns ticket 09 fills are present and defaulted, so 09 needs no migration
    assert r["category_source"] == "none"
    assert r["assigned_account_qbo_id"] is None and r["matched_rule_id"] is None
    # and the seam carries what 09/10 read
    for key in ("bank_account_id", "amount", "date", "description", "payee"):
        assert key in r
