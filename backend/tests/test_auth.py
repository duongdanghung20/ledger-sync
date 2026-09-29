"""Ticket 02: bootstrap, set-password, login, session, logout, /api/me."""

import types
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException

from app.db import get_conn
from app.features.auth import service
from app.features.auth.deps import require_role
from app.main import app

PW = "hunter2hunter"


@pytest.fixture(autouse=True)
def auth_env(monkeypatch):
    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setenv("ORG_NAME", "Acme Books")
    monkeypatch.setenv("SESSION_SECRET", "test-secret-please-change")
    monkeypatch.setenv("APP_BASE_URL", "http://localhost:8080")


@pytest_asyncio.fixture
async def client(pool):
    async def _use_test_pool():
        async with pool.connection() as conn:
            yield conn

    app.dependency_overrides[get_conn] = _use_test_pool
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c
    app.dependency_overrides.clear()


async def _bootstrap(pool) -> str:
    async with pool.connection() as conn:
        return await service.bootstrap(conn)


async def _count(pool, table) -> int:
    async with pool.connection() as conn:
        cur = await conn.execute(f"SELECT count(*) FROM {table}")
        return (await cur.fetchone())[0]


async def test_bootstrap_creates_org_book_admin_and_logs_link(pool, capsys):
    fake_app = types.SimpleNamespace(state=types.SimpleNamespace(pool=pool))

    async with service.bootstrap_lifespan(fake_app):
        pass

    assert "/set-password?token=" in capsys.readouterr().out
    assert await _count(pool, "organizations") == 1
    assert await _count(pool, "books") == 1
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT email, role, password_hash, must_set_password FROM users")
        rows = await cur.fetchall()
    assert len(rows) == 1
    email, role, password_hash, must_set_password = rows[0]
    assert email == "admin@example.com"
    assert role == "Admin"
    assert password_hash is None  # no password ever comes from env/compose
    assert must_set_password is True

    # Idempotent: a second startup creates nothing and prints no new link.
    async with service.bootstrap_lifespan(fake_app):
        pass
    assert "/set-password?token=" not in capsys.readouterr().out
    assert await _count(pool, "users") == 1


async def test_set_password_is_single_use(pool, client):
    token = (await _bootstrap(pool)).split("token=")[1]

    r = await client.post("/api/auth/set-password", json={"token": token, "password": PW})
    assert r.status_code == 200

    async with pool.connection() as conn:
        cur = await conn.execute("SELECT password_hash, must_set_password FROM users")
        password_hash, must_set_password = await cur.fetchone()
    assert password_hash.startswith("$argon2id$")
    assert must_set_password is False

    reuse = await client.post("/api/auth/set-password", json={"token": token, "password": "different1"})
    assert reuse.status_code == 400


async def test_expired_set_password_link_rejected(pool, client):
    await _bootstrap(pool)
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT id FROM users LIMIT 1")
        user_id = (await cur.fetchone())[0]
        await conn.execute(
            "INSERT INTO invitations (user_id, token, expires_at) VALUES (%s, %s, %s)",
            (user_id, "expired-token", datetime.now(timezone.utc) - timedelta(minutes=1)),
        )
    r = await client.post("/api/auth/set-password", json={"token": "expired-token", "password": PW})
    assert r.status_code == 400


async def test_login_sets_httponly_session_and_me_works(pool, client):
    token = (await _bootstrap(pool)).split("token=")[1]
    await client.post("/api/auth/set-password", json={"token": token, "password": PW})

    r = await client.post("/api/auth/login", json={"email": "ADMIN@example.com", "password": PW})
    assert r.status_code == 200
    assert r.json()["email"] == "admin@example.com"
    assert r.json()["role"] == "Admin"

    set_cookie = r.headers.get("set-cookie", "")
    assert "httponly" in set_cookie.lower()
    assert PW not in set_cookie  # cookie carries no readable credential

    me = await client.get("/api/me")  # client persisted the cookie
    assert me.status_code == 200
    assert me.json()["email"] == "admin@example.com"


async def test_login_rejects_bad_password(pool, client):
    token = (await _bootstrap(pool)).split("token=")[1]
    await client.post("/api/auth/set-password", json={"token": token, "password": PW})
    r = await client.post("/api/auth/login", json={"email": "admin@example.com", "password": "wrong-password"})
    assert r.status_code == 401


async def test_me_without_session_is_401(client):
    assert (await client.get("/api/me")).status_code == 401


async def test_expired_and_forged_sessions_are_rejected(pool, client):
    await _bootstrap(pool)
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT id FROM users LIMIT 1")
        user_id = (await cur.fetchone())[0]
        await conn.execute(
            "INSERT INTO sessions (id, user_id, expires_at) VALUES (%s, %s, %s)",
            ("expired-sid", user_id, datetime.now(timezone.utc) - timedelta(days=1)),
        )

    client.cookies.set("session", service.sign_cookie("expired-sid"))
    assert (await client.get("/api/me")).status_code == 401  # expired

    client.cookies.set("session", "expired-sid.deadbeefbadsignature")
    assert (await client.get("/api/me")).status_code == 401  # bad signature


async def test_logout_invalidates_session(pool, client):
    token = (await _bootstrap(pool)).split("token=")[1]
    await client.post("/api/auth/set-password", json={"token": token, "password": PW})
    await client.post("/api/auth/login", json={"email": "admin@example.com", "password": PW})
    assert await _count(pool, "sessions") == 1

    assert (await client.post("/api/auth/logout")).status_code == 200
    assert await _count(pool, "sessions") == 0
    assert (await client.get("/api/me")).status_code == 401


async def test_require_role_guard():
    guard = require_role("Admin")
    admin = {"role": "Admin"}
    assert await guard(user=admin) is admin
    with pytest.raises(HTTPException) as exc:
        await guard(user={"role": "Bookkeeper"})
    assert exc.value.status_code == 403
