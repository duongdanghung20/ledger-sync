"""Auth building blocks: password hashing, sessions, and first-boot bootstrap.

Pure-ish helpers kept out of the HTTP layer so tests (and later tickets) can call
them directly. The session cookie carries an opaque, HMAC-signed session id — no
credential — and every request is validated against the server-side ``sessions``
row (see ``deps.current_user``).
"""

import hashlib
import hmac
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error
from psycopg import AsyncConnection

from app.config import env

SESSION_COOKIE = "session"
SESSION_TTL = timedelta(days=14)  # fixed absolute lifetime, enforced server-side
INVITE_TTL = timedelta(days=7)  # set-password link validity
_BOOTSTRAP_LOCK = 0x1ED6E501  # advisory-lock key so concurrent workers serialize

_ph = PasswordHasher()  # Argon2id with argon2-cffi's RFC 9106 defaults

# Columns returned to clients as the "public" view of a user.
_PUBLIC_COLS = "id, email, role, organization_id, must_set_password"


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except Argon2Error:
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)  # 256 bits of entropy


def _secret() -> bytes:
    return env("SESSION_SECRET").encode()


def sign_cookie(session_id: str) -> str:
    sig = hmac.new(_secret(), session_id.encode(), hashlib.sha256).hexdigest()
    return f"{session_id}.{sig}"


def unsign_cookie(value: str) -> str | None:
    """Return the session id if the signature is valid, else None."""
    session_id, _, sig = value.partition(".")
    if not sig:
        return None
    expected = hmac.new(_secret(), session_id.encode(), hashlib.sha256).hexdigest()
    return session_id if hmac.compare_digest(sig, expected) else None


def cookie_secure() -> bool:
    return env("APP_BASE_URL", "http://localhost:8080").lower().startswith("https")


def to_public(row) -> dict:
    """Map a ``_PUBLIC_COLS`` row (id, email, role, organization_id, must_set_password)."""
    return {
        "id": str(row[0]),
        "email": row[1],
        "role": row[2],
        "organization_id": str(row[3]),
        "must_set_password": row[4],
    }


async def user_public(conn: AsyncConnection, user_id) -> dict:
    cur = await conn.execute(f"SELECT {_PUBLIC_COLS} FROM users WHERE id = %s", (user_id,))
    return to_public(await cur.fetchone())


async def create_session(conn: AsyncConnection, user_id) -> str:
    session_id = new_token()
    expires_at = datetime.now(timezone.utc) + SESSION_TTL
    await conn.execute(
        "INSERT INTO sessions (id, user_id, expires_at) VALUES (%s, %s, %s)",
        (session_id, user_id, expires_at),
    )
    return session_id


def _setup_url(token: str) -> str:
    base = env("APP_BASE_URL", "http://localhost:8080").rstrip("/")
    return f"{base}/set-password?token={token}"


async def bootstrap(conn: AsyncConnection) -> str | None:
    """First-boot seed. Idempotent: creates the Organization, its Book, and the
    first Admin only when the ``users`` table is empty, and returns the single-use
    set-password URL. Returns None if users already exist. Raises if the DB is
    empty but ``BOOTSTRAP_ADMIN_EMAIL`` is unset.
    """
    # Serialize concurrent bootstraps (multiple workers) for the whole txn.
    await conn.execute("SELECT pg_advisory_xact_lock(%s)", (_BOOTSTRAP_LOCK,))

    cur = await conn.execute("SELECT 1 FROM users LIMIT 1")
    if await cur.fetchone():
        return None

    email = env("BOOTSTRAP_ADMIN_EMAIL").strip().lower()
    org_name = env("ORG_NAME", "Ledger-Sync")

    cur = await conn.execute(
        "INSERT INTO organizations (name) VALUES (%s) RETURNING id", (org_name,)
    )
    org_id = (await cur.fetchone())[0]
    await conn.execute(
        "INSERT INTO books (organization_id, name) VALUES (%s, %s)", (org_id, "Default Book")
    )
    cur = await conn.execute(
        "INSERT INTO users (organization_id, email, role, must_set_password) "
        "VALUES (%s, %s, 'Admin', true) RETURNING id",
        (org_id, email),
    )
    user_id = (await cur.fetchone())[0]

    token = new_token()
    expires_at = datetime.now(timezone.utc) + INVITE_TTL
    await conn.execute(
        "INSERT INTO invitations (user_id, token, expires_at) VALUES (%s, %s, %s)",
        (user_id, token, expires_at),
    )
    return _setup_url(token)


def _print_setup_link(url: str) -> None:
    print(
        "\n" + "=" * 72 + "\n"
        "  Ledger-Sync bootstrap: set the first Admin's password within 7 days:\n"
        f"    {url}\n" + "=" * 72 + "\n",
        flush=True,
    )


@asynccontextmanager
async def bootstrap_lifespan(app):
    """Run first-boot bootstrap on startup. Attached to the auth router's
    ``lifespan``; FastAPI merges it into the app lifespan, so it runs after the
    pool on ``app.state.pool`` is open. No edit to ``main.py`` required.
    """
    try:
        async with app.state.pool.connection() as conn:
            url = await bootstrap(conn)
    except RuntimeError as exc:  # e.g. empty DB but BOOTSTRAP_ADMIN_EMAIL unset
        print(f"[bootstrap] skipped: {exc}", flush=True)
        url = None
    if url:
        _print_setup_link(url)
    yield
