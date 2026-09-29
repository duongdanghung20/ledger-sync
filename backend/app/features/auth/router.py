"""Auth HTTP surface: set-password, login, logout, and /api/me.

The router carries ``lifespan=bootstrap_lifespan`` so first-boot bootstrap runs on
startup (FastAPI merges an included router's lifespan into the app's). Routes spell
out the full path after the ``/api`` prefix so ``/api/me`` sits beside ``/api/auth/*``.
"""

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from psycopg import AsyncConnection

from app.db import get_conn
from app.features.auth.deps import current_user
from app.features.auth.service import (
    SESSION_COOKIE,
    SESSION_TTL,
    bootstrap_lifespan,
    cookie_secure,
    create_session,
    hash_password,
    sign_cookie,
    unsign_cookie,
    user_public,
    verify_password,
)

router = APIRouter(prefix="/api", tags=["auth"], lifespan=bootstrap_lifespan)


class SetPasswordIn(BaseModel):
    token: str
    password: str = Field(min_length=8)


class LoginIn(BaseModel):
    email: str
    password: str


@router.post("/auth/set-password")
async def set_password(body: SetPasswordIn, conn: AsyncConnection = Depends(get_conn)) -> dict:
    # Atomic single-use redemption: only an unredeemed, unexpired link matches.
    cur = await conn.execute(
        "UPDATE invitations SET redeemed_at = now() "
        "WHERE token = %s AND redeemed_at IS NULL AND expires_at > now() "
        "RETURNING user_id",
        (body.token,),
    )
    row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=400, detail="Invalid, expired, or already-used link")
    await conn.execute(
        "UPDATE users SET password_hash = %s, must_set_password = false WHERE id = %s",
        (hash_password(body.password), row[0]),
    )
    return {"status": "ok"}


@router.post("/auth/login")
async def login(
    body: LoginIn, response: Response, conn: AsyncConnection = Depends(get_conn)
) -> dict:
    cur = await conn.execute(
        "SELECT id, password_hash, disabled, must_set_password "
        "FROM users WHERE lower(email) = lower(%s)",
        (body.email,),
    )
    row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    user_id, password_hash, disabled, must_set_password = row
    if disabled:
        raise HTTPException(status_code=403, detail="Account disabled")
    if not password_hash or must_set_password or not verify_password(password_hash, body.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    session_id = await create_session(conn, user_id)
    response.set_cookie(
        SESSION_COOKIE,
        sign_cookie(session_id),
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=cookie_secure(),
        path="/",
    )
    return await user_public(conn, user_id)


@router.post("/auth/logout")
async def logout(
    response: Response,
    conn: AsyncConnection = Depends(get_conn),
    session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> dict:
    if session:
        session_id = unsign_cookie(session)
        if session_id:
            await conn.execute("DELETE FROM sessions WHERE id = %s", (session_id,))
    response.delete_cookie(
        SESSION_COOKIE, path="/", httponly=True, samesite="lax", secure=cookie_secure()
    )
    return {"status": "ok"}


@router.get("/me")
async def me(user: dict = Depends(current_user)) -> dict:
    return user
