from fastapi import APIRouter, Depends
from psycopg import AsyncConnection

from app.db import get_conn

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health(conn: AsyncConnection = Depends(get_conn)) -> dict[str, str]:
    """Readiness: the process is up and the database is reachable."""
    await conn.execute("SELECT 1")
    return {"status": "ok"}
