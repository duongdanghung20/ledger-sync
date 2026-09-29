"""User-management helpers for the Admin surface.

Both reuse ticket 02's primitives rather than growing new ones:

* ``mint_setup_link`` issues a set-password link through auth's ``invitations``
  table and URL builder — the *same* single-use link that bootstrap and invites
  hand out, redeemed by the existing ``POST /api/auth/set-password``. No second
  link mechanism, no new table.
* ``revoke_sessions`` drops a user's server-side ``sessions`` rows so an access
  change (disable / role change) takes effect at once instead of at expiry.
"""

from datetime import datetime, timezone

from psycopg import AsyncConnection

from app.features.auth import service as auth


async def mint_setup_link(conn: AsyncConnection, user_id) -> str:
    """Issue a fresh single-use set-password link for an existing user."""
    token = auth.new_token()
    expires_at = datetime.now(timezone.utc) + auth.INVITE_TTL
    await conn.execute(
        "INSERT INTO invitations (user_id, token, expires_at) VALUES (%s, %s, %s)",
        (user_id, token, expires_at),
    )
    return auth._setup_url(token)


async def revoke_sessions(conn: AsyncConnection, user_id) -> None:
    """Invalidate every live session for ``user_id`` immediately."""
    await conn.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))
