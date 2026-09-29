"""Backend test harness.

One ephemeral PostgreSQL (testcontainers) per session, migrated to head. Each
test runs the FastAPI app in-process via httpx ASGITransport and gets a clean
database: the `reset_db` autouse fixture truncates every table after each test.
"""

import os

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from psycopg_pool import AsyncConnectionPool
from testcontainers.community.postgres import PostgresContainer

_BACKEND_ROOT = os.path.dirname(os.path.dirname(__file__))


@pytest.fixture(scope="session")
def database_url():
    # driver=None -> a plain postgresql:// URL that raw psycopg 3 accepts.
    with PostgresContainer("postgres:16", driver=None) as postgres:
        url = postgres.get_connection_url()
        os.environ["DATABASE_URL"] = url
        cfg = Config(os.path.join(_BACKEND_ROOT, "alembic.ini"))
        cfg.set_main_option("script_location", os.path.join(_BACKEND_ROOT, "migrations"))
        command.upgrade(cfg, "head")
        yield url


@pytest_asyncio.fixture
async def pool(database_url):
    async with AsyncConnectionPool(database_url, open=False) as p:
        yield p


@pytest_asyncio.fixture(autouse=True)
async def reset_db(pool):
    yield
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT tablename FROM pg_tables "
            "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
        )
        tables = [row[0] for row in await cur.fetchall()]
        if tables:
            names = ", ".join(f'"{t}"' for t in tables)
            await conn.execute(f"TRUNCATE {names} RESTART IDENTITY CASCADE")
