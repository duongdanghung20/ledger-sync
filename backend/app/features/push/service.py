"""Idempotent QBO push — the money path (ticket 11).

Pushes approved Journal Entries to QuickBooks one at a time, so a retry after ANY
failure — including a process death mid-push — never double-posts. The whole state
machine is here; it only orchestrates seams that already exist (journal snapshot,
qbo port, connection, accounts mirror) — nothing is re-implemented.

Idempotency rests on four things, none invented here:
  * the entry's ``requestid`` (minted once at approval, reused forever) makes a
    replayed POST collapse into the original entry — QBO's native guarantee;
  * a committed ``sync_status='attempting'`` marker written in its OWN transaction
    BEFORE the POST survives a crash: the entry never rolls back to ``pending``, so
    it can never be un-approved and re-approved into a fresh (double-posting)
    ``requestid``. The next push re-drives the stranded ``attempting`` entry;
  * on an ambiguous 5xx/NETWORK — or when re-driving a stranded ``attempting`` — the
    JE may or may not have been created, so we ``find_journal_entry_by_doc_number``
    (accepting only a row carrying THIS entry's ``[ledger-sync:{id}]`` PrivateNote
    stamp) before deciding, never a blind re-post;
  * a per-Book **session-scoped** ``pg_advisory_lock`` serialises concurrent pushes
    across the per-entry commits (a xact-scoped lock would drop at the first
    commit), so two clicks / two people cannot race the same entries. It is released
    in a ``finally`` (``pg_advisory_unlock``).

Public seam (import from ``app.features.push``)::

    push_book(conn, book_id, client) -> summary   # {posted, failed, pending, throttled}
    retry_entry(conn, book_id, journal_entry_id, client) -> row  # one entry, same path

Each entry's state transition is committed on its own (psycopg is non-autocommit;
``conn.commit()`` per step), so a crash leaves at most one entry mid-flight and every
resolved entry durable. Sync status is read back via ticket 10's ``list_journal_entries``.

``last_error_code`` values this module writes: ``validation_inactive_account``,
``validation_unmapped_bank``, ``client_error`` (4xx), ``server_error`` (5xx),
``network``, ``throttled`` (429, entry back to ``pending``).
"""

from __future__ import annotations

from fastapi import HTTPException
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from app.features.accounts import refresh_if_stale
from app.features.journal import get_journal_entry, to_port_entry
from app.features.qbo import Connection, Outcome, QboClient
from app.features.qbo_connection import assert_connected, get_valid_connection

_THROTTLED_MSG = "Rate limited by QuickBooks — retry shortly."

_PUSHABLE_COLS = (
    "id, requestid, doc_number, category_account_qbo_id, cash_account_qbo_id, "
    "amount, debit_side, memo, sync_status"
)


async def _acquire_book_lock(conn: AsyncConnection, book_id) -> None:
    """Serialise the whole push per Book. A **session-scoped** ``pg_advisory_lock``
    is held across the per-entry commits (a xact-scoped lock would drop at the first
    ``commit()``), so a second concurrent push blocks here until the first releases
    it — see ``_release_book_lock``. ``hashtextextended`` maps the Book UUID text to
    the ``bigint`` the lock takes.
    """
    # ponytail: single 64-bit key — a hash collision would only serialise two
    # unrelated Books unnecessarily (a throughput nit, never a correctness bug);
    # add a namespace int if that ever bites.
    await conn.execute(
        "SELECT pg_advisory_lock(hashtextextended(%s::text, 0))", (str(book_id),)
    )


async def _release_book_lock(conn: AsyncConnection, book_id) -> None:
    """Release the session-scoped lock. Roll back first so a half-open or aborted
    transaction can't stop the unlock from running (the lock outlives commits AND
    rollbacks — only an explicit unlock or the session ending drops it)."""
    await conn.rollback()
    await conn.execute(
        "SELECT pg_advisory_unlock(hashtextextended(%s::text, 0))", (str(book_id),)
    )


async def _active_qbo_ids(conn: AsyncConnection, book_id) -> set[str]:
    cur = await conn.execute(
        "SELECT qbo_id FROM accounts WHERE book_id = %s AND active = true", (book_id,)
    )
    return {r[0] for r in await cur.fetchall()}


async def _prepare(
    conn: AsyncConnection, book_id, client: QboClient
) -> tuple[Connection, set[str]]:
    """Everything a push needs before touching an entry: a connected Book (else
    409 blocks the whole push), a live port connection (refreshing the token on
    demand; a failed refresh 409s), a freshly-if-stale mirror, and the set of
    account ids currently active in that mirror."""
    await assert_connected(conn, book_id)
    connection = await get_valid_connection(conn, book_id, client)
    await refresh_if_stale(conn, book_id, client)
    return connection, await _active_qbo_ids(conn, book_id)


def _qbo_id(data) -> str | None:
    if isinstance(data, dict) and data.get("Id") is not None:
        return str(data["Id"])
    return None


async def _mark_attempting(conn: AsyncConnection, je_id) -> None:
    """The crash-surviving marker: committed in its OWN transaction BEFORE the POST.
    Owns the single ``attempt_count`` bump per push (the terminal marks don't bump),
    so one push == one attempt whether it ends posted, failed, or crashed."""
    await conn.execute(
        "UPDATE journal_entries SET sync_status = 'attempting', "
        "last_attempt_at = now(), attempt_count = attempt_count + 1 WHERE id = %s",
        (je_id,),
    )


async def _mark_posted(conn: AsyncConnection, je_id, qbo_id: str | None) -> None:
    await conn.execute(
        "UPDATE journal_entries SET sync_status = 'posted', qbo_id = %s, "
        "posted_at = now(), last_error = NULL, last_error_code = NULL WHERE id = %s",
        (qbo_id, je_id),
    )


async def _mark_failed(conn: AsyncConnection, je_id, code: str, error: str) -> None:
    await conn.execute(
        "UPDATE journal_entries SET sync_status = 'failed', last_error = %s, "
        "last_error_code = %s WHERE id = %s",
        (error, code, je_id),
    )


async def _mark_pending_throttled(conn: AsyncConnection, je_id) -> None:
    # A 429 is rejected BEFORE processing, so the entry was NOT created: return it
    # to pending (safe to re-drive, never in QBO) with a "retry shortly" message.
    # attempt_count was already bumped by the attempting mark.
    await conn.execute(
        "UPDATE journal_entries SET sync_status = 'pending', last_error = %s, "
        "last_error_code = 'throttled' WHERE id = %s",
        (_THROTTLED_MSG, je_id),
    )


async def _resolve_by_doc_number(
    conn: AsyncConnection, connection: Connection, je: dict, client: QboClient
) -> bool:
    """Did THIS entry already post? Query QBO by DocNumber and accept a row only if
    its PrivateNote carries this entry's ``[ledger-sync:{id}]`` stamp — a pre-existing
    QBO JE that happens to share a small-integer DocNumber must never be mis-claimed
    (finding #2). On a match, mark posted (uncommitted) and return True."""
    found = await client.find_journal_entry_by_doc_number(
        connection, str(je["doc_number"])
    )
    if not (found.ok and found.data):
        return False
    stamp = f"[ledger-sync:{je['id']}]"
    match = next(
        (row for row in found.data if stamp in (row.get("PrivateNote") or "")), None
    )
    if match is None:
        return False
    await _mark_posted(conn, je["id"], _qbo_id(match))
    return True


async def _push_one(
    conn: AsyncConnection,
    connection: Connection,
    je: dict,
    active_ids: set[str],
    client: QboClient,
) -> str:
    """Post one entry through the crash-safe state machine, committing each state
    transition on its own. Returns ``posted`` | ``failed`` | ``throttled`` (a
    ``throttled`` result signals the caller to stop the batch)."""
    # a. Pre-push validation against the mirror — a bad entry fails, never blocks
    #    the batch, and never leaves an attempting marker. Category, then cash.
    if je["category_account_qbo_id"] not in active_ids:
        await _mark_failed(
            conn, je["id"], "validation_inactive_account",
            "Target account is inactive or no longer in QuickBooks.",
        )
        await conn.commit()
        return "failed"
    if je["cash_account_qbo_id"] not in active_ids:
        await _mark_failed(
            conn, je["id"], "validation_unmapped_bank",
            "Bank account maps to an account no longer active in QuickBooks.",
        )
        await conn.commit()
        return "failed"

    # b. Commit the crash-surviving 'attempting' marker BEFORE any network call.
    was_attempting = je["sync_status"] == "attempting"
    await _mark_attempting(conn, je["id"])
    await conn.commit()

    # c. A stranded 'attempting' entry (a prior crash) may already be in QBO —
    #    resolve by DocNumber+stamp FIRST, and only POST if it is not there.
    if was_attempting and await _resolve_by_doc_number(conn, connection, je, client):
        await conn.commit()
        return "posted"

    # d. Post with the entry's own requestid (never regenerated) → QBO replay
    #    collapses any duplicate into the original entry.
    result = await client.post_journal_entry(
        connection, to_port_entry(je), str(je["requestid"])
    )

    if result.outcome is Outcome.OK:
        await _mark_posted(conn, je["id"], _qbo_id(result.data))
        await conn.commit()
        return "posted"
    if result.outcome is Outcome.THROTTLED:
        await _mark_pending_throttled(conn, je["id"])
        await conn.commit()
        return "throttled"
    if result.outcome is Outcome.CLIENT_ERROR:
        # 4xx: the entry was NOT created — safe to re-push after a fix.
        await _mark_failed(
            conn, je["id"], "client_error",
            result.error or "QuickBooks rejected the entry (4xx).",
        )
        await conn.commit()
        return "failed"

    # SERVER_ERROR / NETWORK: ambiguous — the JE may or may not exist. Query by
    # DocNumber+stamp before deciding, so a crash-mid-push never double-posts.
    if await _resolve_by_doc_number(conn, connection, je, client):
        await conn.commit()
        return "posted"
    # Not found (or the confirming query itself failed) → failed, retryable. Never
    # re-post blindly: a later push re-drives the 'attempting'→resolve/replay path.
    code = "network" if result.outcome is Outcome.NETWORK else "server_error"
    await _mark_failed(
        conn, je["id"], code, result.error or f"QuickBooks {result.outcome.value}.",
    )
    await conn.commit()
    return "failed"


async def _pushable_entries(conn: AsyncConnection, book_id) -> list[dict]:
    """The Book's ``pending`` OR ``attempting`` entries, oldest first — exactly the
    work a batch push (re-)drives. An ``attempting`` entry is a crash-recovery case:
    a prior push committed the marker before its POST and then died, so it is
    re-driven here (resolved by DocNumber+stamp, or re-POSTed by replay). A
    ``failed`` entry is re-driven only by an explicit ``retry_entry``."""
    async with conn.cursor(row_factory=dict_row) as c:
        await c.execute(
            f"SELECT {_PUSHABLE_COLS} FROM journal_entries "
            f"WHERE book_id = %s AND sync_status IN ('pending', 'attempting') "
            f"ORDER BY created_at, doc_number",
            (book_id,),
        )
        return await c.fetchall()


async def push_book(conn: AsyncConnection, book_id, client: QboClient) -> dict:
    """Push every ``pending`` or ``attempting`` Journal Entry in the Book, one at a
    time, each state transition committed on its own. Returns ``{posted, failed,
    pending, throttled}`` counts. A 429 stops the push immediately, returning that
    entry and leaving the rest ``pending``; already-posted entries keep their state.
    A disconnected/unrefreshable Book blocks the whole push (nothing posted). The
    session-scoped advisory lock is released in the ``finally``."""
    await _acquire_book_lock(conn, book_id)
    try:
        entries = await _pushable_entries(conn, book_id)
        if not entries:
            return {"posted": 0, "failed": 0, "pending": 0, "throttled": False}

        connection, active_ids = await _prepare(conn, book_id, client)

        posted = failed = 0
        throttled = False
        for je in entries:
            status = await _push_one(conn, connection, je, active_ids, client)
            if status == "throttled":
                throttled = True
                break  # stop; this entry + all remaining stay pending
            if status == "posted":
                posted += 1
            else:
                failed += 1

        return {
            "posted": posted,
            "failed": failed,
            "pending": len(entries) - posted - failed,  # throttled + not-yet-attempted
            "throttled": throttled,
        }
    finally:
        await _release_book_lock(conn, book_id)


async def retry_entry(
    conn: AsyncConnection, book_id, journal_entry_id, client: QboClient
) -> dict:
    """Manually re-push one ``failed`` / ``pending`` / stranded ``attempting`` entry
    through the exact same crash-safe path — the same requestid, so no duplicate. An
    already ``posted`` entry is a no-op (returned unchanged). Returns the entry's
    row. The session-scoped advisory lock is released in the ``finally``."""
    await _acquire_book_lock(conn, book_id)
    try:
        async with conn.cursor(row_factory=dict_row) as c:
            await c.execute(
                f"SELECT {_PUSHABLE_COLS} FROM journal_entries WHERE id = %s AND book_id = %s",
                (journal_entry_id, book_id),
            )
            je = await c.fetchone()
        if je is None:
            raise HTTPException(status_code=404, detail="Journal entry not found.")
        if je["sync_status"] != "posted":
            connection, active_ids = await _prepare(conn, book_id, client)
            await _push_one(conn, connection, je, active_ids, client)

        return await get_journal_entry(conn, book_id, journal_entry_id)
    finally:
        await _release_book_lock(conn, book_id)
