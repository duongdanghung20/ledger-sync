# Build conventions

Ticket 01 established these so later tickets add features without editing shared
central files (which would make parallel agents race). Follow them exactly.

## Repository layout

```
/
├── docker-compose.yml        app + db + frontend + proxy, one command
├── Caddyfile                 reverse proxy: /api/* -> backend, else -> frontend
├── .env.example              documented env vars (see "Environment variables")
├── backend/                  FastAPI + psycopg 3 + Alembic
│   ├── app/
│   │   ├── main.py           app factory; AUTO-includes feature routers
│   │   ├── config.py         env() helper (per-module config)
│   │   ├── db.py             async pool + get_conn dependency
│   │   └── features/<name>/  one package per feature
│   │       └── router.py     exposes `router` (APIRouter)
│   ├── migrations/versions/  one Alembic revision file per change
│   └── tests/                pytest; testcontainers Postgres; ASGITransport
└── frontend/                 Next.js (App Router) + Vitest + Playwright
```

## Router self-registration — never edit `main.py` to add routes

A feature is a subpackage of `app.features`. Create
`backend/app/features/<name>/router.py` exposing a module-level `router`:

```python
from fastapi import APIRouter, Depends
from psycopg import AsyncConnection
from app.db import get_conn

router = APIRouter(prefix="/api/<name>", tags=["<name>"])

@router.get("/things")
async def list_things(conn: AsyncConnection = Depends(get_conn)):
    cur = await conn.execute("SELECT ...")
    return await cur.fetchall()
```

`main.py` walks `app.features` at startup and includes every subpackage's
`router`. Add an `__init__.py` to your feature package. Set the full path
(including `/api`) as the router `prefix` so routes land under the proxy's
`/api/*` rule. No central wiring file to touch. See
`app/features/health/router.py` for the working example.

## Per-module config — never edit one central settings class

Read your own environment variables with `app.config.env`:

```python
from app.config import env

def _client_id() -> str:
    return env("QBO_CLIENT_ID")            # raises if unset

org_name = env("ORG_NAME", "Ledger-Sync")  # with default
```

Read the value **where you use it** (or behind an `@lru_cache` function), not at
import time, so a missing variable doesn't stop the whole app from booting.
Document every new variable in the "Added by later tickets" section of
`.env.example` — actually, do NOT edit `.env.example` yourself; report your
variables to the coordinator, who maintains that file.

## Database access

`app.state.pool` is an async psycopg 3 pool opened in the lifespan. Get a
connection via the `get_conn` dependency (above). The connection string is
`DATABASE_URL` (plain `postgresql://`).

## Migrations — one revision file per ticket, no shared edits

Add a schema change with its own revision file:

```bash
cd backend
DATABASE_URL=postgresql://ledger:ledger@localhost:5432/ledger \
  alembic revision -m "add users table"
```

Edit the generated `migrations/versions/<id>_add_users_table.py` (`upgrade()` /
`downgrade()`), using `op.create_table(...)` / `op.execute(...)`. Migrations are
hand-written — there is no ORM to autogenerate from. The baseline
(`0001_baseline`) enables `pgcrypto`, so `gen_random_uuid()` is available for
UUID primary keys.

If two tickets branched from the same head, `alembic heads` shows more than one.
Reconcile once — this does not edit anyone's revision:

```bash
alembic merge -m "merge heads" heads
alembic upgrade head
```

Migrations run automatically on backend container start (`alembic upgrade head`)
and in the test harness before each session.

## Tests

- **Backend:** `cd backend && pytest`. The harness (`tests/conftest.py`) starts a
  disposable Postgres (testcontainers), applies migrations to head, drives the
  app in-process over `httpx.ASGITransport`, and truncates every table after each
  test. Override the `get_conn` dependency to point at the test pool — see
  `tests/test_health.py`. Requires a running Docker daemon. On Docker Desktop
  for Mac, if testcontainers' Ryuk fails to start with a socket mount error,
  run with `DOCKER_HOST=unix:///var/run/docker.sock` (or
  `TESTCONTAINERS_RYUK_DISABLED=true`).
- **Frontend unit:** `cd frontend && npm test` (Vitest + jsdom). Unit tests live
  in `frontend/__tests__/*.test.tsx`.
- **Frontend e2e:** `cd frontend && npm run test:e2e` (Playwright; auto-starts
  `next dev`). Specs live in `frontend/e2e/*.spec.ts`. First run needs
  `npx playwright install chromium`.

## Frontend

App Router under `frontend/app/`. `next.config.ts` uses `output: "standalone"`
for the Docker image. Use the `frontend-design` skill for UI work.

## Environment variables

Canonical list and purpose live in `.env.example`. Skeleton vars: `APP_BASE_URL`,
`PROXY_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `DATABASE_URL`.

## Feature startup hooks — pass `lifespan=` to your router (added by ticket 02)

A feature that must run work on app startup (e.g. one-time seeding) attaches a
lifespan to its own `APIRouter` — no edit to `main.py`. FastAPI merges an included
router's lifespan into the app lifespan, and it runs **after** `main.py`'s lifespan
opens `app.state.pool`, so the pool is available:

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def my_lifespan(app):
    async with app.state.pool.connection() as conn:
        ...  # startup work; keep it idempotent
    yield

router = APIRouter(prefix="/api/<name>", lifespan=my_lifespan)
```

Keep the work idempotent (guard with an existence check and, for multi-worker
deploys, a `pg_advisory_xact_lock`) so it is safe to run once per process. Tests
don't trigger the ASGI lifespan (they drive the app over `ASGITransport`), so call
the underlying startup function directly in tests. See `app/features/auth/service.py`
(`bootstrap` / `bootstrap_lifespan`).
