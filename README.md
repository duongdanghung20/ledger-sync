# Ledger-Sync

Self-hosted tool that turns bank CSV exports into balanced, de-duplicated, double-entry Journal Entries and pushes them to QuickBooks Online — without a mistyped sign, a duplicated row, or a double-post ever corrupting the books. One instance serves one Organization; you run it on your own infrastructure, so no third-party SaaS ever holds your financial data.

A Bookkeeper uploads a bank CSV; Ledger-Sync parses it against a saved per-bank column mapping, de-duplicates it, categorizes each transaction against the company's real QuickBooks accounts, materializes a balanced two-line Journal Entry per transaction on approval, and posts each one idempotently — a retry after any failure never posts a duplicate.

## Key features

- **CSV import from any bank** — a saved, reusable Column-Mapping Profile per bank; auto-suggested from the file's header signature; both signed-amount and separate debit/credit conventions; dot- and comma-decimal formats.
- **Correct de-duplication** — scoped per Bank Account. Uses the bank's stable unique id when present, otherwise a content multiset, so overlapping re-imports never double-import while two genuinely identical same-day transactions are both kept.
- **Rule-based categorization + manual override** — first-match-by-priority rules over payee/description/amount/date; a manual override beats rules and survives re-running categorization.
- **Double-entry by construction** — every approved transaction becomes exactly one balanced two-line Journal Entry (debits equal credits, correct signs), Decimal at two decimal places end to end (never float).
- **Idempotent push** — one entry per call, synchronously, with a durable `attempting` state and QuickBooks `requestid` replay + `DocNumber` recovery, so a crash mid-push is safe and a retry never double-posts.
- **Three interchangeable review views** — a dense Ledger table, a one-at-a-time Focus queue with a debit/credit balance proof, and a Pipeline board — over the same data and operations.
- **Read-only QuickBooks mirror** — the Chart of Accounts is mirrored from QuickBooks and never written back; QuickBooks stays the source of truth for accounts.
- **Self-hosted, one command** — Next.js + FastAPI + PostgreSQL behind a single-origin reverse proxy, packaged with Docker Compose.

---

## Table of Contents

- [Tech Stack](#tech-stack)
- [Prerequisites](#prerequisites)
- [Quick Start (Docker Compose)](#quick-start-docker-compose)
- [First-Boot Bootstrap](#first-boot-bootstrap)
- [Connecting QuickBooks](#connecting-quickbooks)
- [The Workflow](#the-workflow)
- [Local Development (without Docker)](#local-development-without-docker)
- [Architecture](#architecture)
  - [Directory Structure](#directory-structure)
  - [Single-Origin Reverse Proxy](#single-origin-reverse-proxy)
  - [Backend Feature Modules](#backend-feature-modules)
  - [Request Lifecycle](#request-lifecycle)
  - [The Money Path](#the-money-path)
  - [Data Model](#data-model)
  - [Frontend Routes](#frontend-routes)
- [Environment Variables](#environment-variables)
- [Available Commands](#available-commands)
- [Testing](#testing)
- [Deployment](#deployment)
- [Troubleshooting](#troubleshooting)
- [Project Status & Scope](#project-status--scope)
- [License](#license)

---

## Tech Stack

| Layer | Technology |
| --- | --- |
| **Frontend** | Next.js 16 (App Router) · React 19 · TypeScript · CSS Modules |
| **Backend** | FastAPI (Python 3.11+) · psycopg 3 (async) · Alembic |
| **Database** | PostgreSQL 16 |
| **Reverse proxy** | Caddy 2 (single origin: `/api/*` → backend, else → frontend) |
| **Packaging** | Docker Compose |
| **Auth** | Local email/password (Argon2id), httpOnly server-side sessions |
| **Secrets at rest** | `cryptography` Fernet (QuickBooks refresh token) |
| **External API** | QuickBooks Online (`JournalEntry`, `Account`, OAuth2) |
| **Backend tests** | pytest · testcontainers (disposable Postgres) · httpx `ASGITransport` |
| **Frontend tests** | Vitest + Testing Library (jsdom) · Playwright (e2e) |

There is no ORM: SQL is written directly against psycopg 3, and schema changes are hand-written Alembic revisions.

---

## Prerequisites

**To run the whole stack (recommended):**

- **Docker** and **Docker Compose v2** (`docker compose`, not the legacy `docker-compose`).

That is all you need to run Ledger-Sync.

**To develop or run the test suites locally:**

- **Python 3.11+** (a virtualenv in `backend/.venv` is assumed by the docs).
- **Node.js 20+** and **npm**.
- **A running Docker daemon** — the backend test suite spins up a disposable PostgreSQL via testcontainers.
- **PostgreSQL 16** (or just use the `db` container from Docker Compose) if running the backend outside Docker.

**To actually post to QuickBooks:**

- A **QuickBooks Online** company (a sandbox company works for development).
- An app registered in the [Intuit Developer](https://developer.intuit.com/) portal, giving you a **client id** and **client secret**. See [Connecting QuickBooks](#connecting-quickbooks).

---

## Quick Start (Docker Compose)

### 1. Clone

```bash
git clone git@github.com:duongdanghung20/ledger-sync.git
cd ledger-sync
```

### 2. Create your `.env`

```bash
cp .env.example .env
```

Then edit `.env`. At minimum, set a bootstrap admin and the two required secrets:

```bash
# Who becomes the first Admin on first boot
BOOTSTRAP_ADMIN_EMAIL=you@example.com

# Signs the session cookie — generate a long random value
SESSION_SECRET=$(openssl rand -hex 32)

# Encrypts the QuickBooks refresh token at rest — generate a Fernet key
TOKEN_ENC_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
```

`QBO_CLIENT_ID` / `QBO_CLIENT_SECRET` can stay blank until you are ready to connect QuickBooks. Every variable is documented in [Environment Variables](#environment-variables).

### 3. Bring the stack up

```bash
docker compose up --build
```

This starts four services:

| Service | Image / Build | Port |
| --- | --- | --- |
| `db` | `postgres:16` | internal `5432` (data persisted in the `db-data` volume) |
| `backend` | `./backend` (FastAPI) | internal `8000`; runs `alembic upgrade head` on start |
| `frontend` | `./frontend` (Next.js) | internal `3000` |
| `proxy` | `caddy:2` | **published `${PROXY_PORT:-8080}` → 80** |

Everything is reached through the proxy at **`APP_BASE_URL`** (default `http://localhost:8080`). The frontend and API share this one origin — that is a hard requirement of the session-cookie design, not a convenience (see [Single-Origin Reverse Proxy](#single-origin-reverse-proxy)).

### 4. Verify it is up

```bash
curl http://localhost:8080/api/health
# {"status":"ok"}
```

Open **http://localhost:8080** in a browser. You will be sent to the login screen — but first you need to set the first Admin's password. Continue to [First-Boot Bootstrap](#first-boot-bootstrap).

---

## First-Boot Bootstrap

There is **no open signup screen**. On the very first boot with an empty database, the backend reads `BOOTSTRAP_ADMIN_EMAIL`, creates the Organization, its single Book, and the first Admin User, and **prints a single-use, expiring set-password link to the container logs**. No password is ever stored in a compose file or an environment variable.

Grab the link from the logs:

```bash
docker compose logs backend | grep -i set-password
```

You will see a line containing a URL like:

```
http://localhost:8080/set-password?token=<opaque-token>
```

Open that URL, set a password, and log in at `http://localhost:8080/login`. The link is single-use and expires; if it lapses, an existing Admin can reissue one from the Users screen (or, for the very first Admin, wipe the volume and re-bootstrap — see Troubleshooting).

From here an Admin can invite more Users (each gets its own set-password link to hand over out-of-band — there is no email/SMTP in v1), change roles, and disable people. Disabling a User or changing their Role revokes their live sessions immediately.

### Roles

| Role | Can do |
| --- | --- |
| **Admin** | Everything a Bookkeeper can, **plus** QuickBooks Connection management, User management, and Bank Account creation/mapping. |
| **Bookkeeper** | The full operational money path: import, categorize, refresh accounts, review, approve, push. |

Authorization is enforced **server-side on every route** — a tampered frontend cannot reach an Admin-only endpoint.

---

## Connecting QuickBooks

1. Register an app at [developer.intuit.com](https://developer.intuit.com/). Under its OAuth 2.0 keys, note the **client id** and **client secret**.
2. Add the redirect URI **exactly** as `{APP_BASE_URL}/api/qbo/callback` — e.g. `http://localhost:8080/api/qbo/callback` in development.
3. Put the credentials in `.env` and restart:

   ```bash
   QBO_CLIENT_ID=...
   QBO_CLIENT_SECRET=...
   ```

   ```bash
   docker compose up -d --build backend
   ```

4. Log in as an Admin, go to **Settings → QuickBooks** (`/settings/quickbooks`), and click **Connect to QuickBooks**. You will be sent to Intuit's consent screen and redirected back; the connection captures the `realm_id` and stores the tokens (the refresh token encrypted at rest with `TOKEN_ENC_KEY`).
5. The Chart of Accounts is mirrored on connect. If a connection later fails to refresh, its status flips to **disconnected**, pushes are blocked, and the screen shows a **Reconnect** prompt.

App credentials and the encryption key live only in the environment — never in the database or the UI.

---

## The Workflow

1. **Admin** connects QuickBooks and creates each **Bank Account** (checking, card), mapping it one-to-one to its cash-side QuickBooks Account (`/admin/bank-accounts`).
2. **Bookkeeper** uploads a bank CSV (`/import`). Ledger-Sync suggests the matching Column-Mapping Profile from the header signature, parses the rows into canonical Imported Transactions, de-duplicates them per Bank Account, and shows any rejected rows or possible duplicates rather than guessing.
3. **Categorization Rules** (`/rules`) assign each transaction one target Account automatically; the Bookkeeper manually categorizes or overrides the rest.
4. **Review** (the home page, `/`) in whichever of three views you prefer — Ledger table, Focus queue, or Pipeline board. Approving a transaction materializes its balanced Journal Entry.
5. **Push** the approved entries to QuickBooks. Each push is idempotent and shows per-entry Sync Status (pending / posted / failed); a failed entry surfaces the QuickBooks error and offers a safe retry.

---

## Local Development (without Docker)

You can run the two apps directly for a faster edit loop. You still need a PostgreSQL to point at — the simplest is the Compose `db` service:

```bash
docker compose up -d db
```

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e '.[test]'

# Point at the db container (published on localhost if you expose 5432, or run Postgres locally)
export DATABASE_URL=postgresql://ledger:ledger@localhost:5432/ledger
export APP_BASE_URL=http://localhost:8080
export SESSION_SECRET=dev-secret
export BOOTSTRAP_ADMIN_EMAIL=you@example.com

alembic upgrade head          # apply migrations
uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install

# In dev the frontend needs /api proxied to the backend (in production Caddy does this).
# next.config.ts rewrites /api/* to API_PROXY_TARGET when it is set:
export API_PROXY_TARGET=http://localhost:8000
npm run dev                   # http://localhost:3000
```

Open `http://localhost:3000`. (For the cookie/session flow to behave exactly as in production, prefer running the full stack through the proxy — the single-origin assumption matters; see below.)

---

## Architecture

### Directory Structure

```
/
├── docker-compose.yml         db + backend + frontend + proxy, one command
├── Caddyfile                  reverse proxy: /api/* → backend, else → frontend
├── .env.example               documented environment variables
├── CONTEXT.md                 domain glossary (the canonical vocabulary)
├── docs/
│   ├── adr/                   architecture decision records (0001–0005)
│   └── agents/                build conventions + issue-tracker notes
├── backend/                   FastAPI + psycopg 3 + Alembic
│   ├── app/
│   │   ├── main.py            app factory; AUTO-includes every feature router
│   │   ├── config.py          env() helper (per-module config)
│   │   ├── db.py              async psycopg pool + get_conn dependency
│   │   └── features/<name>/   one self-contained package per feature
│   │       ├── router.py      exposes `router` (APIRouter, prefix incl. /api)
│   │       ├── service.py     DB + orchestration
│   │       └── engine.py      pure logic (where a feature has it)
│   ├── migrations/versions/   one hand-written Alembic revision per change
│   ├── tests/                 pytest; testcontainers Postgres; ASGITransport
│   └── e2e_server.py          throwaway stack for the Playwright e2e
└── frontend/                  Next.js App Router + Vitest + Playwright
    ├── app/                   routes + review views
    ├── components/            shared UI (Nav, …)
    ├── lib/review.ts          shared review data + the four operations
    ├── __tests__/             Vitest component tests
    └── e2e/                   Playwright specs
```

A feature is a subpackage of `app.features`. `main.py` walks that package at startup and includes each subpackage's `router` — **no central wiring file is ever edited to add routes.** Full conventions are in [`docs/agents/conventions.md`](docs/agents/conventions.md).

### Single-Origin Reverse Proxy

Caddy publishes one port and routes by path:

```
:80
  handle /api/*   → reverse_proxy backend:8000   (path preserved; API routes live under /api)
  handle          → reverse_proxy frontend:3000
```

The frontend and API are therefore **same-origin**. This is what lets the session live in a plain httpOnly cookie with no CORS and no cross-site cookie gymnastics — and it is why `APP_BASE_URL` must be the URL you actually reach the proxy at.

### Backend Feature Modules

| Module | Route prefix | Responsibility |
| --- | --- | --- |
| `health` | `/api/health` | Liveness probe. |
| `auth` | `/api/auth/*`, `/api/me` | Bootstrap first Admin, login/logout, Argon2id passwords, httpOnly server-side sessions (14-day absolute expiry), single-use set-password links; the `current_user` / `require_role` dependencies. |
| `users` | `/api/users*` | Admin-only User CRUD, role change, disable, password-reset link reissue; immediate session revocation on disable/role change. |
| `qbo` | *(no routes)* | The single QuickBooks client port + an in-memory fake. Every outbound QuickBooks call goes through here; nothing else touches `httpx`. |
| `qbo_connection` | `/api/qbo/*` | Per-Book OAuth authorization-code flow, callback, `realm_id` capture, refresh-token encryption at rest, on-demand token refresh, connection status. |
| `accounts` | `/api/accounts*` | Read-only Chart of Accounts mirror: fetch, reconcile (upsert by `qbo_id`, absent → inactive), lazy refresh, categorization-target selection (excludes A/R and A/P). |
| `bank_accounts` | `/api/bank-accounts*` | Admin creates each Bank Account and maps it 1:1 to its cash-side Account. |
| `csv_import` | `/api/csv-import/*` | Column-Mapping Profile CRUD, CSV parse (stdlib `csv`), per-Bank-Account multiset dedup, malformed-row rejection. |
| `categorization` | `/api/categorization/*` | The pure rule matcher, rule CRUD, manual override, invalid-target flagging. |
| `journal` | `/api/journal/*` | Materialize a balanced two-line Journal Entry at approval; the sign rule; un-approve; debit/credit preview. |
| `push` | `/api/push*` | The idempotent one-per-call synchronous push, Sync Status transitions, pre-push validation, retry classification, per-Book advisory lock. |
| `review` | `/api/review` | The derived-state read model the three frontend views render over. |

### Request Lifecycle

```
Browser → Caddy (:8080)
        ├── /api/*  → FastAPI (:8000)
        │             → session-validation dependency (httpOnly cookie → server-side Session)
        │             → per-route RBAC dependency (require_role, where applicable)
        │             → feature router → service → psycopg 3 → PostgreSQL
        │             → (for QuickBooks) the qbo client port → QuickBooks Online
        └── else    → Next.js (:3000) → React (App Router) → fetches /api/* same-origin
```

### The Money Path

The correctness bar. These invariants are held strictly and covered by dedicated tests:

- **Dedup** never double-imports and never drops a genuinely distinct transaction. Keyed on the bank's `external_id` when present, otherwise a content multiset over `(date, signed amount, description, payee)`, scoped per Bank Account.
- **Balance & sign** — for signed canonical amount `A` (negative = outflow of the Bank Account):

  ```
  A < 0 (outflow):  Debit category |A|,  Credit cash     |A|
  A > 0 (inflow):   Debit cash     |A|,  Credit category |A|
  A = 0:            rejected (no zero-amount Journal Entry)
  ```

  Both lines post `abs(A)` with opposite `PostingType`, so debits equal credits by construction. The rule is account-type-agnostic — transfers, refunds, and card paydowns need no special case.
- **Decimal, never float** — amounts are two-decimal-place `Decimal` from CSV parse through the database (`numeric(14,2)`) to the wire.
- **Idempotency** — a `requestid` is minted once at approval and reused on every retry, never regenerated. Push commits a durable `attempting` state *before* the QuickBooks POST, so a crash between the POST and the result commit is recoverable: the next push queries by `DocNumber` (verified against a `[ledger-sync:<id>]` stamp in the entry's `PrivateNote`) and resolves to posted or failed — it never blindly re-posts. A `429` stops the push and leaves the rest pending. Concurrent pushes on the same Book are serialized by a PostgreSQL advisory lock. `unapprove` is allowed only while an entry is still `pending`, so an in-flight entry's `requestid` can never be discarded.

### Data Model

PostgreSQL, one Organization per instance, all financial records scoped to a Book. Primary keys are UUIDs (`gen_random_uuid()`, via `pgcrypto`).

| Table | Key columns |
| --- | --- |
| `organizations` | id, name |
| `books` | id, organization_id, name, last_synced_at |
| `users` | id, email (unique, case-insensitive), password_hash (Argon2id), role (`Admin`/`Bookkeeper`), disabled, must_set_password |
| `sessions` | id (opaque), user_id, created_at, expires_at (14-day absolute) |
| `invitations` | id, email, role, token, expires_at, redeemed_at |
| `quickbooks_connections` | id, book_id (1:1), realm_id, access token + expiry, **encrypted** refresh token, status (`pending`/`connected`/`disconnected`), oauth_state, connected_at |
| `accounts` (CoA mirror) | id, book_id, qbo_id, name, account_type, account_sub_type, classification, acct_num, active, sync_token, parent_qbo_id — unique `(book_id, qbo_id)` |
| `bank_accounts` | id, book_id, name, **`qbo_account_id`** (required, the mapped cash-side Account) |
| `column_mapping_profiles` | id, book_id, name, bank_account_id (default), config (JSONB) |
| `imported_transactions` | id, book_id, bank_account_id, column_mapping_profile_id, date, signed `amount` `numeric(14,2)`, description, payee, external_id, imported_at, raw (JSONB), dedup_key, assigned_account_qbo_id, category_source (`rule`/`manual`/`none`), matched_rule_id |
| `categorization_rules` | id, book_id, priority, target_qbo_account_id, conditions (JSONB), invalid_target |
| `journal_entries` | id, imported_transaction_id (1:1), category_account_qbo_id + cash_account_qbo_id (snapshots), amount (abs, 2dp), debit_side, memo, sync_status (`pending`/`attempting`/`posted`/`failed`), qbo_id, requestid, doc_number, posted_at, last_error, last_error_code, last_attempt_at, attempt_count |
| `journal_doc_counters` | book_id, value — the atomic per-Book `doc_number` sequence |

There is **no** Journal Entry lines table: the two lines are reconstructed from the snapshot. **Transaction state is derived, not stored** — uncategorized (no assigned account) → categorized (assigned) → approved (a `pending` Journal Entry exists) → posted / failed (the entry's `sync_status`).

Binding decisions are recorded as ADRs in [`docs/adr/`](docs/adr/) (single-tenant · QuickBooks is the source of truth for accounts · Book as the accounting-entity boundary · local passwords · idempotent one-per-call push). Domain vocabulary lives in [`CONTEXT.md`](CONTEXT.md).

### Frontend Routes

| Route | Purpose | Access |
| --- | --- | --- |
| `/` | Review workspace — switch between Ledger table, Focus queue, Pipeline board (remembered in `localStorage`) | Bookkeeper |
| `/login`, `/set-password` | Authentication | Public |
| `/import` | Upload CSV, confirm profile + Bank Account, review imported/rejected/possible-duplicate | Bookkeeper |
| `/rules` | Categorization rule management | Bookkeeper |
| `/admin/users` | User & role management | Admin |
| `/admin/bank-accounts` | Bank Account creation + mapping | Admin |
| `/settings/quickbooks` | Connection status + Connect/Reconnect | Admin |

---

## Environment Variables

Copy `.env.example` to `.env` and fill it in. Docker Compose interpolates this file, and the backend also reads it.

### Required

| Variable | Description | How to set |
| --- | --- | --- |
| `BOOTSTRAP_ADMIN_EMAIL` | Email of the first Admin, created on first empty boot. | Your email. |
| `SESSION_SECRET` | HMAC key that signs the session cookie. No default by design. | `openssl rand -hex 32` |
| `TOKEN_ENC_KEY` | Fernet key encrypting the QuickBooks refresh token at rest; kept out of the database. | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |

### Required to post to QuickBooks

| Variable | Description | How to set |
| --- | --- | --- |
| `QBO_CLIENT_ID` | QuickBooks Online app client id. | Intuit Developer portal. |
| `QBO_CLIENT_SECRET` | QuickBooks Online app client secret. | Intuit Developer portal. |

### Infrastructure (sensible defaults)

| Variable | Description | Default |
| --- | --- | --- |
| `APP_BASE_URL` | Public origin the whole app is served at (also fixes the OAuth redirect `{APP_BASE_URL}/api/qbo/callback`). | `http://localhost:8080` |
| `PROXY_PORT` | Host port the reverse proxy publishes (container listens on 80). Keep in sync with `APP_BASE_URL`. | `8080` |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | Configure the `db` container. | `ledger` / `ledger` / `ledger` |
| `DATABASE_URL` | How the backend and Alembic connect (plain `postgresql://`). Keep consistent with `POSTGRES_*`. | `postgresql://ledger:ledger@db:5432/ledger` |
| `ORG_NAME` | Organization name at bootstrap; editable later. | `Ledger-Sync` |

### Development / e2e only

| Variable | Description |
| --- | --- |
| `QBO_FAKE` | When `1`, swaps the QuickBooks client for an in-process fake and seeds a connected Book. **Default off; never enable in production.** Used only by the Playwright e2e. |
| `API_PROXY_TARGET` | When set, `next.config.ts` rewrites `/api/*` to this target for local dev without Caddy. Unset in production (the proxy handles it). |

Secrets (`SESSION_SECRET`, `TOKEN_ENC_KEY`, `QBO_CLIENT_SECRET`) and the database password must be real, unique values in production. `.env` is git-ignored; never commit it.

---

## Available Commands

### Docker Compose

| Command | Description |
| --- | --- |
| `docker compose up --build` | Build and start the whole stack. |
| `docker compose up -d` | Start detached. |
| `docker compose logs -f backend` | Follow backend logs (where the bootstrap link is printed). |
| `docker compose down` | Stop and remove containers (keeps the `db-data` volume). |
| `docker compose down -v` | Stop and **wipe the database volume** (fresh bootstrap next time). |

### Backend (`cd backend`)

| Command | Description |
| --- | --- |
| `uvicorn app.main:app --reload --port 8000` | Run the API with autoreload. |
| `alembic upgrade head` | Apply all migrations. |
| `alembic downgrade -1` | Roll back the last migration. |
| `alembic revision -m "add X"` | Create a new (hand-written) revision file. |
| `alembic heads` | Show migration head(s); more than one means a branch to `alembic merge`. |
| `pytest` | Run the backend test suite (needs Docker). |
| `python e2e_server.py` | Throwaway migrated Postgres + uvicorn with `QBO_FAKE` on (for the e2e). |

### Frontend (`cd frontend`)

| Command | Description |
| --- | --- |
| `npm run dev` | Next.js dev server on `:3000`. |
| `npm run build` | Production build (`output: "standalone"`). |
| `npm start` | Serve the production build. |
| `npm test` | Vitest unit/component tests. |
| `npm run test:watch` | Vitest in watch mode. |
| `npm run test:e2e` | Playwright end-to-end tests. |

---

## Testing

Three seams, exercised in priority order.

### 1. Backend — API + faked QuickBooks (the bulk of coverage)

```bash
cd backend
pytest
```

The harness (`tests/conftest.py`) starts a **disposable PostgreSQL via testcontainers**, applies migrations to head, drives the FastAPI app in-process over `httpx.ASGITransport` against real Postgres, and truncates every table after each test. The **only** faked external boundary is the QuickBooks client port; everything else runs for real. A running Docker daemon is required — testcontainers is not optional, because the per-Book push lock uses a real Postgres advisory lock and the money tables rely on real constraints.

> **macOS / Docker Desktop:** if testcontainers' Ryuk container fails to start with a socket-mount error, run:
> ```bash
> DOCKER_HOST=unix:///var/run/docker.sock TESTCONTAINERS_RYUK_DISABLED=true pytest
> ```

Money-path tests are the ones that matter most: dedup correctness (never double-imports / never drops identicals), debit-equals-credit balance and correct signs, and no-double-post idempotency (requestid replay, `DocNumber` crash recovery, 429 handling, pre-push validation).

### 2. Frontend — unit / component

```bash
cd frontend
npm test              # Vitest + Testing Library (jsdom)
```

### 3. End-to-end (Playwright over the running stack)

```bash
# Terminal 1 — throwaway backend with fake QuickBooks (needs Docker)
cd backend && .venv/bin/python e2e_server.py

# Terminal 2 — the browser tests
cd frontend
npx playwright install chromium   # first run only
npm run test:e2e
```

This drives the real browser through the happy path: import → categorize → approve → push → Posted, against the in-process QuickBooks fake.

---

## Deployment

Ledger-Sync is designed to be self-hosted with the same Docker Compose stack you develop against. There is no cloud-provider lock-in and no external SaaS dependency beyond QuickBooks itself.

### Production checklist

1. **Real secrets.** Generate fresh `SESSION_SECRET` and `TOKEN_ENC_KEY`, set a strong `POSTGRES_PASSWORD`, and set `QBO_CLIENT_ID` / `QBO_CLIENT_SECRET`. Keep `.env` off version control and off the image.
2. **Public URL + TLS.** Set `APP_BASE_URL` to your real HTTPS origin (e.g. `https://ledger.example.com`) and register `{APP_BASE_URL}/api/qbo/callback` as the Intuit redirect URI. Terminate TLS at the proxy — the bundled `Caddyfile` disables auto-HTTPS for the local single-port setup; for a public host, front it with a Caddy site block that has a real hostname (Caddy will obtain a certificate automatically) or put it behind your own TLS-terminating proxy. The session cookie's `secure` flag is enabled automatically when `APP_BASE_URL` starts with `https`.
3. **Persistent database.** The `db-data` named volume holds all financial data — back it up. For managed Postgres, point `DATABASE_URL` at it and drop the `db` service.
4. **Migrations.** The backend runs `alembic upgrade head` on start, so deploying a new image applies pending migrations automatically.
5. **Bring it up.**

   ```bash
   docker compose up -d --build
   ```

6. **Bootstrap** the first Admin (see [First-Boot Bootstrap](#first-boot-bootstrap)) and **connect QuickBooks** (see [Connecting QuickBooks](#connecting-quickbooks)).

### Backups

```bash
# Dump
docker compose exec db pg_dump -U ledger ledger > ledger-$(date +%F).sql

# Restore (into a fresh, empty database)
cat ledger-YYYY-MM-DD.sql | docker compose exec -T db psql -U ledger ledger
```

> **CI/CD** is intentionally out of scope for v1 (see [Project Status & Scope](#project-status--scope)); wire up your own pipeline (build image → run `pytest` + `npm test` → push → `docker compose up -d`) as needed.

---

## Troubleshooting

### `curl /api/health` fails / the app doesn't load

- Confirm all four services are healthy: `docker compose ps`. The `backend` waits for `db` to pass its healthcheck before starting.
- Check backend logs: `docker compose logs backend`. A missing required env var (`SESSION_SECRET` on login, `TOKEN_ENC_KEY` on connect) surfaces at the point of use, not at boot.
- Make sure you are hitting the **proxy** (`:8080`), not the backend directly — the frontend is only reachable through the proxy.

### I never saw the set-password link

```bash
docker compose logs backend | grep -i set-password
```

If the database already has Users, bootstrap does not run again. To start completely fresh:

```bash
docker compose down -v && docker compose up --build   # wipes the db-data volume
```

### QuickBooks connect fails or redirects to an error

- The Intuit redirect URI must match `{APP_BASE_URL}/api/qbo/callback` **exactly** (scheme, host, port, path).
- `QBO_CLIENT_ID` / `QBO_CLIENT_SECRET` must be set and the backend restarted after setting them.
- After connecting, if pushes are blocked with a Reconnect prompt, the refresh token could not be renewed — click **Reconnect** to re-authorize.

### Backend tests error with a Docker / Ryuk socket message (macOS)

```bash
DOCKER_HOST=unix:///var/run/docker.sock TESTCONTAINERS_RYUK_DISABLED=true pytest
```

### `alembic heads` shows more than one head

Two migrations branched from the same parent. Reconcile without editing anyone's revision:

```bash
cd backend
alembic merge -m "merge heads" heads
alembic upgrade head
```

### Playwright can't launch a browser

```bash
cd frontend && npx playwright install chromium
```

On some older OS versions the bundled Chromium won't install; point Playwright at a system Chrome via its config, or run the e2e on Linux/CI.

### A push shows `failed`

Open the entry in any review view — it shows the QuickBooks error and `last_error_code`. Common codes: `validation_inactive_account` / `validation_unmapped_bank` (fix the mapping or reactivate the account in QuickBooks, then retry), `client_error` (a 4xx from QuickBooks — fix and re-push), `throttled` (rate-limited — retry shortly). Retrying reuses the same `requestid`, so it never double-posts.

---

## Project Status & Scope

v1 is complete: all features above are implemented and tested (backend API + service seams under pytest/testcontainers, frontend under Vitest, and a Playwright happy-path e2e).

**Deliberately out of scope for v1** (see the spec in `.scratch/v1-spec/spec.md` and the ADRs):

- CI/CD pipeline · in-app financial reporting (QuickBooks owns reporting) · editing/reversing already-posted entries · multi-tenancy / SaaS · ML auto-categorization · reading existing QuickBooks transactions back · split transactions (one Account per transaction) · multi-currency (home currency only) · multi-Book UI / per-Book roles · email/SMTP (invitations use out-of-band links) · external identity providers.

**Known ceilings** (accepted, documented in code with `ponytail:` comments): CSV upload is transported as JSON rather than multipart; the per-Book advisory lock uses a single 64-bit key (throughput, not correctness); dedup without a bank unique id is inherently ambiguous across non-aligned re-import windows and surfaces the ambiguity rather than guessing.

---

## License

No license file is present yet. Add one (`LICENSE`) before distributing.
