"""Admin user management: invite, change role, disable/enable, reset password.

Every route is Admin-only — guarded server-side by ``require_role("Admin")`` so a
tampered or Bookkeeper frontend cannot reach it — and scoped to the caller's own
organization. Reuses ticket 02's invitation link primitive and ``sessions`` table;
no new table and no second link mechanism.
"""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from psycopg import AsyncConnection
from pydantic import BaseModel, model_validator

from app.db import get_conn
from app.features.auth.deps import require_role
from app.features.auth.service import to_public
from app.features.users.service import mint_setup_link, revoke_sessions

router = APIRouter(prefix="/api/users", tags=["users"])

Role = Literal["Admin", "Bookkeeper"]

# id, email, role, organization_id, must_set_password (-> to_public) + disabled.
_COLS = "id, email, role, organization_id, must_set_password, disabled"


def _serialize(row) -> dict:
    return {**to_public(row[:5]), "disabled": row[5]}


class InviteIn(BaseModel):
    email: str
    role: Role


class UpdateUserIn(BaseModel):
    role: Role | None = None
    disabled: bool | None = None

    @model_validator(mode="after")
    def _something_to_do(self):
        if self.role is None and self.disabled is None:
            raise ValueError("Provide at least one of: role, disabled")
        return self


@router.get("")
async def list_users(
    admin: dict = Depends(require_role("Admin")),
    conn: AsyncConnection = Depends(get_conn),
) -> list[dict]:
    cur = await conn.execute(
        f"SELECT {_COLS} FROM users WHERE organization_id = %s ORDER BY created_at",
        (admin["organization_id"],),
    )
    return [_serialize(row) for row in await cur.fetchall()]


@router.post("/invite", status_code=201)
async def invite_user(
    body: InviteIn,
    admin: dict = Depends(require_role("Admin")),
    conn: AsyncConnection = Depends(get_conn),
) -> dict:
    email = body.email.strip().lower()
    cur = await conn.execute("SELECT 1 FROM users WHERE lower(email) = %s", (email,))
    if await cur.fetchone():
        raise HTTPException(status_code=409, detail="A user with that email already exists")
    cur = await conn.execute(
        "INSERT INTO users (organization_id, email, role, must_set_password, disabled) "
        f"VALUES (%s, %s, %s, true, false) RETURNING {_COLS}",
        (admin["organization_id"], email, body.role),
    )
    row = await cur.fetchone()
    link = await mint_setup_link(conn, row[0])
    return {"user": _serialize(row), "setup_link": link}


async def _get_in_org(conn: AsyncConnection, user_id: UUID, org_id):
    cur = await conn.execute(
        f"SELECT {_COLS} FROM users WHERE id = %s AND organization_id = %s",
        (user_id, org_id),
    )
    row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="User not found")
    return row


@router.patch("/{user_id}")
async def update_user(
    user_id: UUID,
    body: UpdateUserIn,
    admin: dict = Depends(require_role("Admin")),
    conn: AsyncConnection = Depends(get_conn),
) -> dict:
    await _get_in_org(conn, user_id, admin["organization_id"])
    sets, params = [], []
    if body.role is not None:
        sets.append("role = %s")
        params.append(body.role)
    if body.disabled is not None:
        sets.append("disabled = %s")
        params.append(body.disabled)
    params.append(user_id)
    cur = await conn.execute(
        f"UPDATE users SET {', '.join(sets)} WHERE id = %s RETURNING {_COLS}", params
    )
    # Any access change revokes the user's live sessions at once. (Re-enabling is a
    # no-op — a disabled user had none — so unconditional revoke is safe and simplest.)
    await revoke_sessions(conn, user_id)
    return _serialize(await cur.fetchone())


@router.post("/{user_id}/reset")
async def reset_password(
    user_id: UUID,
    admin: dict = Depends(require_role("Admin")),
    conn: AsyncConnection = Depends(get_conn),
) -> dict:
    await _get_in_org(conn, user_id, admin["organization_id"])
    # ponytail: reset only reissues the link (per AC). It does not force-expire the
    # old password or revoke sessions — only disable/role-change do that.
    return {"setup_link": await mint_setup_link(conn, user_id)}
