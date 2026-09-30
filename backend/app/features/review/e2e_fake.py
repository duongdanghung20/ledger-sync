"""Env-gated fake-QuickBooks mode for the running-stack e2e — default OFF, prod-safe.

Set ``QBO_FAKE=1`` and, on startup, this:

  1. swaps the injected ``get_qbo_client`` (push + accounts routers) to a single
     in-process ``FakeQboClient`` via ``app.dependency_overrides`` — so the push
     path posts to memory, never Intuit, and no OAuth is needed;
  2. seeds a review-ready Book for the bootstrap Organization (idempotent): a
     *connected* QBO connection, the mirror refreshed from the fake's Chart of
     Accounts, one Bank Account mapped to the fake's cash account, a default
     Column-Mapping Profile, and a ready Bookkeeper login.

None of this touches another feature's source: the client swap is a dependency
override, the seed is plain INSERTs guarded by existence checks. When the var is
unset (production) the lifespan is a no-op. It attaches to the review router's
own lifespan, so ``main.py`` is never edited.

Requires ``TOKEN_ENC_KEY`` (to encrypt the fake refresh token) and the bootstrap
vars (``BOOTSTRAP_ADMIN_EMAIL`` / ``ORG_NAME``) to be set in the e2e backend env.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from app.config import env

# e2e login fixtures — referenced by the Playwright spec.
E2E_BOOKKEEPER_EMAIL = "bookkeeper@example.com"
E2E_BOOKKEEPER_PASSWORD = "ledger-sync-e2e"

# A default profile whose columns match the CSV the e2e uploads.
_E2E_PROFILE_CONFIG = {
    "date": {"column": "Date", "format": "YYYY-MM-DD"},
    "description": {"column": "Description"},
    "amount": {"mode": "signed", "column": "Amount"},
    "decimal": "dot",
}


def _enabled() -> bool:
    return env("QBO_FAKE", "").strip().lower() in ("1", "true", "yes", "on")


@asynccontextmanager
async def fake_qbo_lifespan(app):
    """Runs after main.py opens the pool and after auth bootstrap seeds the Book."""
    if not _enabled():
        yield
        return

    from app.features.accounts.router import get_qbo_client as accounts_client
    from app.features.push.router import get_qbo_client as push_client
    from app.features.qbo import FakeQboClient

    fake = FakeQboClient()  # one instance: its in-memory journal store persists
    app.dependency_overrides[push_client] = lambda: fake
    app.dependency_overrides[accounts_client] = lambda: fake

    async with app.state.pool.connection() as conn:
        await _seed(conn, fake)

    print("[qbo-fake] e2e fake-QuickBooks mode ON — Book seeded, client swapped.", flush=True)
    yield


async def _seed(conn, fake) -> None:
    from app.features.accounts import refresh
    from app.features.auth.service import hash_password
    from app.features.qbo_connection import crypto

    row = await (await conn.execute(
        "SELECT id, organization_id FROM books ORDER BY created_at LIMIT 1"
    )).fetchone()
    if row is None:
        print("[qbo-fake] no Book yet (bootstrap skipped?) — nothing seeded.", flush=True)
        return
    book_id, org_id = row

    # 1. A connected QBO connection with a non-expired token (so no OAuth / refresh).
    await conn.execute(
        "INSERT INTO quickbooks_connections "
        "(book_id, realm_id, access_token, access_token_expires_at, "
        " refresh_token_encrypted, status, connected_at) "
        "VALUES (%s, 'fake-realm', 'fake-access', %s, %s, 'connected', now()) "
        "ON CONFLICT (book_id) DO NOTHING",
        (book_id, datetime.now(timezone.utc) + timedelta(hours=1),
         crypto.encrypt("fake-refresh")),
    )

    # 2. Populate the mirror from the fake's Chart of Accounts.
    await refresh(conn, book_id, fake)

    # 3. A Bank Account mapped to the fake's active cash account ("1" = Checking).
    has_bank = await (await conn.execute(
        "SELECT 1 FROM bank_accounts WHERE book_id = %s LIMIT 1", (book_id,)
    )).fetchone()
    if not has_bank:
        await conn.execute(
            "INSERT INTO bank_accounts (book_id, name, qbo_account_id) "
            "VALUES (%s, 'Checking', '1')",
            (book_id,),
        )

    # 4. A default Column-Mapping Profile matching the e2e CSV's header.
    from psycopg.types.json import Jsonb
    has_profile = await (await conn.execute(
        "SELECT 1 FROM column_mapping_profiles WHERE book_id = %s LIMIT 1", (book_id,)
    )).fetchone()
    if not has_profile:
        await conn.execute(
            "INSERT INTO column_mapping_profiles (book_id, name, config) "
            "VALUES (%s, 'E2E Bank', %s)",
            (book_id, Jsonb(_E2E_PROFILE_CONFIG)),
        )

    # 5. A ready Bookkeeper login (no set-password step in the e2e).
    has_bk = await (await conn.execute(
        "SELECT 1 FROM users WHERE email = %s", (E2E_BOOKKEEPER_EMAIL,)
    )).fetchone()
    if not has_bk:
        await conn.execute(
            "INSERT INTO users (organization_id, email, password_hash, role, must_set_password) "
            "VALUES (%s, %s, %s, 'Bookkeeper', false)",
            (org_id, E2E_BOOKKEEPER_EMAIL, hash_password(E2E_BOOKKEEPER_PASSWORD)),
        )
