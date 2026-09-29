"""Database access.

The async connection pool lives on `app.state.pool` (opened in the lifespan in
`main.py`). Feature routes get a pooled connection through the `get_conn`
dependency:

    from fastapi import Depends
    from psycopg import AsyncConnection
    from app.db import get_conn

    @router.get("/things")
    async def list_things(conn: AsyncConnection = Depends(get_conn)):
        cur = await conn.execute("SELECT ...")
        ...

Tests point this at an ephemeral database by overriding `get_conn` in
`app.dependency_overrides` — see `tests/test_health.py`.
"""

from fastapi import Request


async def get_conn(request: Request):
    async with request.app.state.pool.connection() as conn:
        yield conn
