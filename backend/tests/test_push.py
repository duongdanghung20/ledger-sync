"""Ticket 11: idempotent push + Sync Status — the strict money-path bar.

Every branch is driven through the FakeQboClient via ``fail_next``. The one
invariant under test everywhere: a retry after ANY failure never double-posts —
asserted by counting the JEs the fake actually created.

Covered:
* retry replays the SAME requestid and returns the original entry — one JE only;
* crash mid-push (5xx/network) recovered by query-by-DocNumber → posted or failed,
  never a duplicate;
* pre-push validation skips an inactive-target / unmapped-bank entry as failed with
  the specific code while the rest of the batch keeps posting;
* a 429 stops the push, remaining entries stay pending, already-posted stay posted;
* a 4xx fails and is safe to re-push after a fix;
* concurrent pushes on one Book are serialised by the advisory lock;
* a disconnected connection blocks the whole push;
* sync fields transition correctly.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from fastapi import HTTPException
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.db import get_conn
from app.features.auth import service as auth_service
from app.features.journal import to_port_entry
from app.features.push import service
from app.features.push.router import get_qbo_client
from app.features.qbo import Connection, FakeQboClient, Outcome
from app.features.qbo_connection import crypto
from app.main import app


@pytest.fixture(autouse=True)
def env(monkeypatch):
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


# --- setup helpers ----------------------------------------------------------


async def _setup(ctx, *, connected=True):
    """Bootstrap, log in a Bookkeeper (cookie set), seed a connected QBO
    connection with a non-expired token, and a mirror with an active cash account
    + two active category accounts and a bank account mapped to the cash account.
    ``last_synced_at`` is stamped fresh so ``refresh_if_stale`` skips (the mirror
    stays exactly what we seed). Returns (book_id, bank_account_id)."""
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
            "VALUES (%s, '9999', 'access-tok', %s, %s, %s, now())",
            (book_id, datetime.now(timezone.utc) + timedelta(hours=1),
             crypto.encrypt("refresh-tok"), "connected" if connected else "disconnected"),
        )
    ctx.client.cookies.set("session", auth_service.sign_cookie(sid))

    await _account(ctx.pool, book_id, "cash-1", "Checking", atype="Bank")
    await _account(ctx.pool, book_id, "exp-meals", "Meals")
    await _account(ctx.pool, book_id, "exp-office", "Office")
    ba = await _bank_account(ctx.pool, book_id, "cash-1")
    await _stamp_synced(ctx.pool, book_id)
    return book_id, ba


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


async def _tx(pool, book_id, ba, *, amount, category="exp-meals", description="TXN"):
    async with pool.connection() as conn:
        row = await (await conn.execute(
            "INSERT INTO imported_transactions "
            "(book_id, bank_account_id, date, amount, description, raw, dedup_key, "
            " assigned_account_qbo_id, category_source) "
            "VALUES (%s, %s, '2026-09-02', %s, %s, %s, %s, %s, 'manual') RETURNING id",
            (book_id, ba, Decimal(amount), description, Jsonb({}),
             description + amount, category),
        )).fetchone()
    return str(row[0])


async def _stamp_synced(pool, book_id):
    async with pool.connection() as conn:
        await conn.execute("UPDATE books SET last_synced_at = now() WHERE id = %s", (book_id,))


async def _set_active(pool, book_id, qbo_id, active):
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE accounts SET active = %s WHERE book_id = %s AND qbo_id = %s",
            (active, book_id, qbo_id),
        )


async def _approve(ctx, book_id, ba, *, amount="-6.75", category="exp-meals"):
    """Materialize one pending JE via the journal approve endpoint. Returns its
    json (id, requestid, doc_number, ...)."""
    tx = await _tx(ctx.pool, book_id, ba, amount=amount, category=category)
    r = await ctx.client.post(f"/api/journal/transactions/{tx}/approve")
    assert r.status_code == 201, r.text
    return r.json()


async def _je(pool, je_id):
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as c:
            await c.execute("SELECT * FROM journal_entries WHERE id = %s", (je_id,))
            return await c.fetchone()


async def _push(pool, book_id, fake):
    async with pool.connection() as conn:
        return await service.push_book(conn, book_id, fake)


# =========================================================================== #
#  Money-path: retry replays the same requestid — never a duplicate            #
# =========================================================================== #


async def test_retry_replays_same_requestid_no_duplicate(ctx):
    book_id, ba = await _setup(ctx)
    je = await _approve(ctx, book_id, ba)

    # First attempt hits a 4xx: NOT created.
    ctx.fake.fail_next(Outcome.CLIENT_ERROR)
    await _push(ctx.pool, book_id, ctx.fake)
    assert (await _je(ctx.pool, je["id"]))["sync_status"] == "failed"
    assert len(ctx.fake._journals) == 0

    # Retry with the SAME requestid → creates exactly one JE.
    async with ctx.pool.connection() as conn:
        row = await service.retry_entry(conn, book_id, je["id"], ctx.fake)
    assert row["sync_status"] == "posted"
    assert len(ctx.fake._journals) == 1

    # Retry AGAIN → QBO replays the same requestid → still exactly one JE.
    async with ctx.pool.connection() as conn:
        row = await service.retry_entry(conn, book_id, je["id"], ctx.fake)
    assert row["sync_status"] == "posted"
    assert len(ctx.fake._journals) == 1  # NO second entry
    # The requestid that replayed is the one minted at approval, unchanged.
    assert str(row["requestid"]) == je["requestid"]
    assert (str(ctx.fake._journals[0].get("DocNumber"))) == str(je["doc_number"])


# =========================================================================== #
#  Money-path: crash mid-push recovered by query-by-DocNumber                  #
# =========================================================================== #


async def test_5xx_with_je_already_created_resolves_to_posted_no_duplicate(ctx):
    """QBO created the JE but the response was lost (a real ambiguous crash):
    query-by-DocNumber finds it → posted, and no second entry is created."""
    book_id, ba = await _setup(ctx)
    je = await _approve(ctx, book_id, ba)
    full = await _je(ctx.pool, je["id"])

    # Simulate "QBO already has this JE" (under its DocNumber) before the push.
    seeded = await ctx.fake.post_journal_entry(
        Connection("9999", "a", "r"), to_port_entry(full), "seed-requestid"
    )
    assert seeded.ok and len(ctx.fake._journals) == 1

    # The push's POST returns 5xx (ambiguous); query-by-DocNumber resolves it.
    ctx.fake.fail_next(Outcome.SERVER_ERROR)
    summary = await _push(ctx.pool, book_id, ctx.fake)
    assert summary["posted"] == 1

    row = await _je(ctx.pool, je["id"])
    assert row["sync_status"] == "posted"
    assert row["qbo_id"] == ctx.fake._journals[0]["Id"]
    assert len(ctx.fake._journals) == 1  # NO duplicate created by the failed POST


async def test_5xx_with_no_je_created_resolves_to_failed_then_retry_posts_once(ctx):
    """5xx and the JE was never created: query finds nothing → failed (retryable).
    A later retry posts exactly one JE — never a double-post."""
    book_id, ba = await _setup(ctx)
    je = await _approve(ctx, book_id, ba)

    ctx.fake.fail_next(Outcome.SERVER_ERROR)
    await _push(ctx.pool, book_id, ctx.fake)
    row = await _je(ctx.pool, je["id"])
    assert row["sync_status"] == "failed" and row["last_error_code"] == "server_error"
    assert len(ctx.fake._journals) == 0  # nothing created

    async with ctx.pool.connection() as conn:
        row = await service.retry_entry(conn, book_id, je["id"], ctx.fake)
    assert row["sync_status"] == "posted"
    assert len(ctx.fake._journals) == 1


async def test_network_error_resolves_via_docnumber_query(ctx):
    book_id, ba = await _setup(ctx)
    je = await _approve(ctx, book_id, ba)
    ctx.fake.fail_next(Outcome.NETWORK)
    await _push(ctx.pool, book_id, ctx.fake)
    row = await _je(ctx.pool, je["id"])
    assert row["sync_status"] == "failed" and row["last_error_code"] == "network"
    assert len(ctx.fake._journals) == 0


# =========================================================================== #
#  The closed hole: a crash between the POST and the DB commit                 #
# =========================================================================== #


async def test_crash_after_post_leaves_attempting_unapprove_refuses_recovers_no_double(ctx):
    """The double-post hole, closed. The POST reaches QBO (JE created) but the
    process dies before the result commits. Because ``sync_status='attempting'`` was
    committed in its OWN transaction BEFORE the POST, the entry does NOT roll back to
    pending: it is stranded ``attempting``. unapprove then REFUSES it (so its
    requestid can't be discarded and re-minted), and the next push resolves it via
    DocNumber+stamp to posted with the SAME requestid — exactly one JE in QBO."""
    book_id, ba = await _setup(ctx)
    je = await _approve(ctx, book_id, ba)

    ctx.fake.crash_next_post()  # POST reaches QBO, then the process dies
    with pytest.raises(RuntimeError):
        await _push(ctx.pool, book_id, ctx.fake)

    row = await _je(ctx.pool, je["id"])
    assert row["sync_status"] == "attempting"       # crash-surviving marker, NOT pending
    assert len(ctx.fake._journals) == 1             # QBO already holds the JE
    original_requestid = str(row["requestid"])

    # unapprove REFUSES the attempting entry (409) — its requestid is preserved.
    r = await ctx.client.post(f"/api/journal/entries/{je['id']}/unapprove")
    assert r.status_code == 409
    assert await _je(ctx.pool, je["id"]) is not None

    # Next push re-drives the stranded attempting entry → resolved via DocNumber+stamp.
    summary = await _push(ctx.pool, book_id, ctx.fake)
    assert summary["posted"] == 1
    resolved = await _je(ctx.pool, je["id"])
    assert resolved["sync_status"] == "posted"
    assert str(resolved["requestid"]) == original_requestid   # never regenerated
    assert resolved["qbo_id"] == ctx.fake._journals[0]["Id"]
    assert len(ctx.fake._journals) == 1             # exactly ONE JE — no double-post


async def test_created_but_response_lost_recovers_within_same_push_no_duplicate(ctx):
    """The response-lost twin: the POST creates the JE but returns 5xx. The same push
    resolves it by DocNumber+stamp → posted, no duplicate. Exercises the fake's
    ``store_first`` fidelity (the JE exists though the caller saw a failure)."""
    book_id, ba = await _setup(ctx)
    je = await _approve(ctx, book_id, ba)

    ctx.fake.fail_next(Outcome.SERVER_ERROR, store_first=True)
    summary = await _push(ctx.pool, book_id, ctx.fake)
    assert summary["posted"] == 1

    row = await _je(ctx.pool, je["id"])
    assert row["sync_status"] == "posted"
    assert row["qbo_id"] == ctx.fake._journals[0]["Id"]
    assert len(ctx.fake._journals) == 1             # created once, recovered, no dup


async def test_docnumber_recovery_ignores_je_without_this_entrys_stamp(ctx):
    """finding #2: a pre-existing QBO JE sharing this entry's small-integer DocNumber
    but carrying a foreign PrivateNote must NOT be mis-claimed as ours. DocNumber
    recovery filters on the ``[ledger-sync:{id}]`` stamp → resolves to failed, not a
    wrong posted."""
    book_id, ba = await _setup(ctx)
    je = await _approve(ctx, book_id, ba)
    full = await _je(ctx.pool, je["id"])

    ctx.fake._journals.append({
        "Id": "pre-existing",
        "DocNumber": str(full["doc_number"]),
        "PrivateNote": "some other bookkeeper's manual entry",  # no ledger-sync stamp
        "Line": [],
    })

    ctx.fake.fail_next(Outcome.SERVER_ERROR)  # ambiguous → DocNumber recovery runs
    summary = await _push(ctx.pool, book_id, ctx.fake)
    assert summary["failed"] == 1 and summary["posted"] == 0

    row = await _je(ctx.pool, je["id"])
    assert row["sync_status"] == "failed"
    assert row["last_error_code"] == "server_error"
    assert row["qbo_id"] is None                    # not attributed to the foreign JE


async def test_429_returns_entry_to_pending_and_is_unapprovable_again(ctx):
    """A 429 is rejected before processing (never in QBO), so the entry returns to
    pending — and, being safely never-posted, is un-approvable again."""
    book_id, ba = await _setup(ctx)
    je = await _approve(ctx, book_id, ba)

    ctx.fake.fail_next(Outcome.THROTTLED)
    summary = await _push(ctx.pool, book_id, ctx.fake)
    assert summary["throttled"] is True
    row = await _je(ctx.pool, je["id"])
    assert row["sync_status"] == "pending"          # back to pending, NOT attempting
    assert len(ctx.fake._journals) == 0             # never reached QBO

    r = await ctx.client.post(f"/api/journal/entries/{je['id']}/unapprove")
    assert r.status_code == 200
    assert await _je(ctx.pool, je["id"]) is None    # safely discarded


# =========================================================================== #
#  Pre-push validation skips a bad entry; the batch continues                  #
# =========================================================================== #


async def test_inactive_target_account_fails_that_entry_batch_continues(ctx):
    book_id, ba = await _setup(ctx)
    bad = await _approve(ctx, book_id, ba, category="exp-meals")
    good = await _approve(ctx, book_id, ba, category="exp-office")

    # exp-meals goes inactive in the mirror after approval → the bad entry can't post.
    await _set_active(ctx.pool, book_id, "exp-meals", False)

    summary = await _push(ctx.pool, book_id, ctx.fake)
    assert summary == {"posted": 1, "failed": 1, "pending": 0, "throttled": False}

    bad_row = await _je(ctx.pool, bad["id"])
    good_row = await _je(ctx.pool, good["id"])
    assert bad_row["sync_status"] == "failed"
    assert bad_row["last_error_code"] == "validation_inactive_account"
    assert good_row["sync_status"] == "posted"          # batch kept going
    assert len(ctx.fake._journals) == 1                 # only the good one posted


async def test_unmapped_bank_account_fails_with_specific_code(ctx):
    book_id, ba = await _setup(ctx)
    je = await _approve(ctx, book_id, ba)
    await _set_active(ctx.pool, book_id, "cash-1", False)  # cash side no longer active

    await _push(ctx.pool, book_id, ctx.fake)
    row = await _je(ctx.pool, je["id"])
    assert row["sync_status"] == "failed"
    assert row["last_error_code"] == "validation_unmapped_bank"
    assert len(ctx.fake._journals) == 0


# =========================================================================== #
#  429 stops the push; already-posted stay posted; remaining stay pending      #
# =========================================================================== #


async def test_429_stops_push_leaves_remaining_pending_posted_stay_posted(ctx):
    book_id, ba = await _setup(ctx)
    posted_je = await _approve(ctx, book_id, ba)
    # First push posts entry 1 cleanly.
    assert (await _push(ctx.pool, book_id, ctx.fake))["posted"] == 1
    assert (await _je(ctx.pool, posted_je["id"]))["sync_status"] == "posted"

    # Two more pending entries; the next push is throttled on the first of them.
    a = await _approve(ctx, book_id, ba)
    b = await _approve(ctx, book_id, ba)
    ctx.fake.fail_next(Outcome.THROTTLED)
    summary = await _push(ctx.pool, book_id, ctx.fake)

    assert summary["throttled"] is True
    assert summary["posted"] == 0 and summary["pending"] == 2  # a + b left pending
    assert (await _je(ctx.pool, a["id"]))["sync_status"] == "pending"
    assert (await _je(ctx.pool, b["id"]))["sync_status"] == "pending"
    # Already-posted entry is untouched; only the one JE from the first push exists.
    assert (await _je(ctx.pool, posted_je["id"]))["sync_status"] == "posted"
    assert len(ctx.fake._journals) == 1
    # The throttled entry carries the "retry shortly" message.
    throttled_row = await _je(ctx.pool, a["id"])
    assert "retry shortly" in throttled_row["last_error"].lower()


# =========================================================================== #
#  4xx fails; re-push after a fix succeeds                                      #
# =========================================================================== #


async def test_4xx_fails_then_repush_after_fix_posts(ctx):
    book_id, ba = await _setup(ctx)
    je = await _approve(ctx, book_id, ba)

    ctx.fake.fail_next(Outcome.CLIENT_ERROR)
    await _push(ctx.pool, book_id, ctx.fake)
    row = await _je(ctx.pool, je["id"])
    assert row["sync_status"] == "failed" and row["last_error_code"] == "client_error"
    assert row["qbo_id"] is None and len(ctx.fake._journals) == 0

    # "Fix" and retry (no forced failure) → posts.
    async with ctx.pool.connection() as conn:
        row = await service.retry_entry(conn, book_id, je["id"], ctx.fake)
    assert row["sync_status"] == "posted" and len(ctx.fake._journals) == 1


# =========================================================================== #
#  Concurrent pushes serialised by the advisory lock                           #
# =========================================================================== #


async def test_concurrent_pushes_serialized_no_double_post(ctx):
    book_id, ba = await _setup(ctx)
    je = await _approve(ctx, book_id, ba)

    # Two overlapping pushes on the same Book, each its own connection/txn.
    r1, r2 = await asyncio.gather(
        _push(ctx.pool, book_id, ctx.fake),
        _push(ctx.pool, book_id, ctx.fake),
    )

    # The advisory lock serialises them: exactly one push posts the entry, the
    # other acquires the lock second, sees it already posted, and does nothing.
    assert sorted([r1["posted"], r2["posted"]]) == [0, 1]
    assert (await _je(ctx.pool, je["id"]))["sync_status"] == "posted"
    assert len(ctx.fake._journals) == 1  # never double-posted


# =========================================================================== #
#  Disconnected connection blocks the whole push                               #
# =========================================================================== #


async def test_disconnected_connection_blocks_push(ctx):
    book_id, ba = await _setup(ctx, connected=False)
    je = await _approve(ctx, book_id, ba)

    with pytest.raises(HTTPException) as exc:
        await _push(ctx.pool, book_id, ctx.fake)
    assert exc.value.status_code == 409

    assert (await _je(ctx.pool, je["id"]))["sync_status"] == "pending"  # nothing posted
    assert len(ctx.fake._journals) == 0


# =========================================================================== #
#  Sync-field transitions                                                      #
# =========================================================================== #


async def test_sync_fields_transition_on_success(ctx):
    book_id, ba = await _setup(ctx)
    je = await _approve(ctx, book_id, ba)
    before = await _je(ctx.pool, je["id"])
    assert before["sync_status"] == "pending" and before["attempt_count"] == 0

    await _push(ctx.pool, book_id, ctx.fake)
    row = await _je(ctx.pool, je["id"])
    assert row["sync_status"] == "posted"
    assert row["qbo_id"] is not None and row["posted_at"] is not None
    assert row["last_error"] is None and row["last_error_code"] is None
    assert row["last_attempt_at"] is not None and row["attempt_count"] == 1


async def test_sync_fields_transition_on_failure(ctx):
    book_id, ba = await _setup(ctx)
    je = await _approve(ctx, book_id, ba)

    ctx.fake.fail_next(Outcome.CLIENT_ERROR)
    await _push(ctx.pool, book_id, ctx.fake)
    row = await _je(ctx.pool, je["id"])
    assert row["sync_status"] == "failed"
    assert row["last_error"] and row["last_error_code"] == "client_error"
    assert row["last_attempt_at"] is not None and row["attempt_count"] == 1
    assert row["qbo_id"] is None and row["posted_at"] is None


# =========================================================================== #
#  HTTP surface                                                                #
# =========================================================================== #


async def test_push_route_posts_pending_and_reports_summary(ctx):
    book_id, ba = await _setup(ctx)
    await _approve(ctx, book_id, ba)
    await _approve(ctx, book_id, ba, category="exp-office")

    r = await ctx.client.post("/api/push")
    assert r.status_code == 200, r.text
    assert r.json() == {"posted": 2, "failed": 0, "pending": 0, "throttled": False}
    assert len(ctx.fake._journals) == 2


async def test_retry_route_returns_updated_sync_status(ctx):
    book_id, ba = await _setup(ctx)
    je = await _approve(ctx, book_id, ba)
    ctx.fake.fail_next(Outcome.CLIENT_ERROR)
    await ctx.client.post("/api/push")

    r = await ctx.client.post(f"/api/push/entries/{je['id']}/retry")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sync_status"] == "posted" and body["qbo_id"] is not None
    assert body["attempt_count"] == 2  # one failed push + one successful retry


async def test_push_routes_require_authentication(ctx):
    app.dependency_overrides.pop(get_conn, None)  # exercise the real auth guard
    async def _use_pool():
        async with ctx.pool.connection() as conn:
            yield conn
    app.dependency_overrides[get_conn] = _use_pool
    ctx.client.cookies.clear()
    assert (await ctx.client.post("/api/push")).status_code == 401
    assert (await ctx.client.post(
        "/api/push/entries/00000000-0000-0000-0000-000000000000/retry"
    )).status_code == 401
