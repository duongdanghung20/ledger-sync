"""Ticket 03: Admin user management + immediate session revocation.

Bootstraps the first Admin (ticket 02), redeems the link to get a working login,
then drives the /api/users routes. A separate httpx client per user gives each its
own cookie jar, so we can prove that an access change kills another user's *live*
session at once (not just their next login).
"""

import httpx
import pytest
import pytest_asyncio

from app.db import get_conn
from app.features.auth import service
from app.main import app

PW = "hunter2hunter"


@pytest.fixture(autouse=True)
def auth_env(monkeypatch):
    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setenv("ORG_NAME", "Acme Books")
    monkeypatch.setenv("SESSION_SECRET", "test-secret-please-change")
    monkeypatch.setenv("APP_BASE_URL", "http://localhost:8080")


@pytest_asyncio.fixture
async def make(pool):
    """Factory for independent clients (separate cookie jars) sharing the app."""

    async def _use_test_pool():
        async with pool.connection() as conn:
            yield conn

    app.dependency_overrides[get_conn] = _use_test_pool
    transport = httpx.ASGITransport(app=app)
    made: list[httpx.AsyncClient] = []

    async def _make() -> httpx.AsyncClient:
        c = httpx.AsyncClient(transport=transport, base_url="http://testserver")
        made.append(c)
        return c

    yield _make
    for c in made:
        await c.aclose()
    app.dependency_overrides.clear()


async def _login_admin(client, pool):
    async with pool.connection() as conn:
        url = await service.bootstrap(conn)
    token = url.split("token=")[1]
    await client.post("/api/auth/set-password", json={"token": token, "password": PW})
    r = await client.post("/api/auth/login", json={"email": "admin@example.com", "password": PW})
    assert r.status_code == 200


async def _admin_and_live_bookkeeper(make, pool):
    """Admin (authed) + a Bookkeeper who has set a password and holds a live session."""
    admin = await make()
    await _login_admin(admin, pool)
    r = await admin.post("/api/users/invite", json={"email": "book@example.com", "role": "Bookkeeper"})
    assert r.status_code == 201
    uid = r.json()["user"]["id"]
    token = r.json()["setup_link"].split("token=")[1]
    await admin.post("/api/auth/set-password", json={"token": token, "password": PW})
    book = await make()
    assert (await book.post("/api/auth/login", json={"email": "book@example.com", "password": PW})).status_code == 200
    assert (await book.get("/api/me")).status_code == 200  # live session
    return admin, book, uid


async def test_invite_returns_link_that_redeems_into_a_user(make, pool):
    admin = await make()
    await _login_admin(admin, pool)

    r = await admin.post("/api/users/invite", json={"email": "New@Example.com", "role": "Bookkeeper"})
    assert r.status_code == 201
    body = r.json()
    assert body["user"]["email"] == "new@example.com"  # normalized
    assert body["user"]["role"] == "Bookkeeper"
    assert body["user"]["must_set_password"] is True
    assert body["user"]["disabled"] is False
    assert "/set-password?token=" in body["setup_link"]

    # The invited person becomes a usable User on redemption of the reused link.
    token = body["setup_link"].split("token=")[1]
    assert (await admin.post("/api/auth/set-password", json={"token": token, "password": PW})).status_code == 200
    login = await admin.post("/api/auth/login", json={"email": "new@example.com", "password": PW})
    assert login.status_code == 200
    assert login.json()["role"] == "Bookkeeper"


async def test_duplicate_email_invite_conflicts(make, pool):
    admin = await make()
    await _login_admin(admin, pool)
    await admin.post("/api/users/invite", json={"email": "dup@example.com", "role": "Bookkeeper"})
    r = await admin.post("/api/users/invite", json={"email": "DUP@example.com", "role": "Admin"})
    assert r.status_code == 409


async def test_list_returns_org_members(make, pool):
    admin, _book, _uid = await _admin_and_live_bookkeeper(make, pool)
    r = await admin.get("/api/users")
    assert r.status_code == 200
    emails = {u["email"] for u in r.json()}
    assert {"admin@example.com", "book@example.com"} <= emails


async def test_disable_revokes_live_session_and_blocks_login(make, pool):
    admin, book, uid = await _admin_and_live_bookkeeper(make, pool)

    r = await admin.patch(f"/api/users/{uid}", json={"disabled": True})
    assert r.status_code == 200
    assert r.json()["disabled"] is True

    # The token that worked a moment ago is now rejected — no wait for expiry.
    assert (await book.get("/api/me")).status_code == 401
    # And a disabled user cannot log back in.
    assert (await book.post("/api/auth/login", json={"email": "book@example.com", "password": PW})).status_code == 403


async def test_role_change_revokes_live_session(make, pool):
    admin, book, uid = await _admin_and_live_bookkeeper(make, pool)

    r = await admin.patch(f"/api/users/{uid}", json={"role": "Admin"})
    assert r.status_code == 200
    assert r.json()["role"] == "Admin"

    # Previously valid session stops working at once.
    assert (await book.get("/api/me")).status_code == 401
    # Re-login reflects the new role.
    await book.post("/api/auth/login", json={"email": "book@example.com", "password": PW})
    assert (await book.get("/api/me")).json()["role"] == "Admin"


async def test_reset_reissues_a_working_link(make, pool):
    admin, book, uid = await _admin_and_live_bookkeeper(make, pool)

    r = await admin.post(f"/api/users/{uid}/reset")
    assert r.status_code == 200
    link = r.json()["setup_link"]
    assert "/set-password?token=" in link

    token = link.split("token=")[1]
    assert (await book.post("/api/auth/set-password", json={"token": token, "password": "brandnew123"})).status_code == 200
    assert (await book.post("/api/auth/login", json={"email": "book@example.com", "password": "brandnew123"})).status_code == 200


async def test_every_admin_route_rejects_a_bookkeeper(make, pool):
    _admin, book, uid = await _admin_and_live_bookkeeper(make, pool)
    # `book` is authenticated as a Bookkeeper — each Admin-only route must 403.
    assert (await book.get("/api/users")).status_code == 403
    assert (
        await book.post("/api/users/invite", json={"email": "x@example.com", "role": "Bookkeeper"})
    ).status_code == 403
    assert (await book.patch(f"/api/users/{uid}", json={"disabled": True})).status_code == 403
    assert (await book.post(f"/api/users/{uid}/reset")).status_code == 403


async def test_admin_routes_require_authentication(make):
    anon = await make()
    assert (await anon.get("/api/users")).status_code == 401
    assert (await anon.post("/api/users/invite", json={"email": "x@example.com", "role": "Admin"})).status_code == 401
