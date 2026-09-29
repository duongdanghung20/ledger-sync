"""Reusable auth dependencies for every protected route in later tickets.

    from app.features.auth.deps import current_user, require_role

    @router.get("/things")
    async def list_things(user=Depends(current_user)): ...

    @router.post("/admin-thing")
    async def admin_thing(user=Depends(require_role("Admin"))): ...

``current_user`` validates the server-side session behind the httpOnly cookie and
returns the public user dict (``id, email, role, organization_id, must_set_password``);
it 401s when the session is missing, forged, expired, or gone, and 403s a disabled
user. ``require_role`` builds a guard that additionally asserts the user's role.
"""

from fastapi import Cookie, Depends, HTTPException
from psycopg import AsyncConnection

from app.db import get_conn
from app.features.auth.service import SESSION_COOKIE, to_public, unsign_cookie


async def current_user(
    conn: AsyncConnection = Depends(get_conn),
    session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> dict:
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    session_id = unsign_cookie(session)
    if not session_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    cur = await conn.execute(
        "SELECT u.id, u.email, u.role, u.organization_id, u.must_set_password, u.disabled "
        "FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.id = %s AND s.expires_at > now()",
        (session_id,),
    )
    row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if row[5]:  # disabled
        raise HTTPException(status_code=403, detail="Account disabled")
    return to_public(row[:5])


def require_role(*roles: str):
    async def guard(user: dict = Depends(current_user)) -> dict:
        if user["role"] not in roles:
            raise HTTPException(status_code=403, detail="Insufficient role")
        return user

    return guard
