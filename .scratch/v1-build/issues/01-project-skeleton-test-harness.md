# 01: Project skeleton & test harness

**What to build:** A self-hostable skeleton of the whole stack that comes up in one command, plus the test harness every later slice runs on. An operator runs `docker compose up` and gets FastAPI, Next.js, PostgreSQL, and a reverse proxy that serves the frontend and the API at a single origin (`APP_BASE_URL`) — the same-origin assumption the session-cookie design later depends on. A health endpoint is reachable through the proxy. The backend test harness drives the FastAPI app in-process against a disposable PostgreSQL (testcontainers) with migrations applied and state reset per test; the frontend has its unit and end-to-end runners scaffolded. No product features yet — this exists so every later slice can land green. This is a prefactor: make the change easy, then make the easy change.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

- [ ] `docker compose up` starts app, database, and reverse proxy; the frontend and API answer on one origin at `APP_BASE_URL`.
- [ ] `GET /api/health` returns 200 through the reverse proxy (not only against the backend directly).
- [ ] A migration tool is wired; a baseline migration applies cleanly on a fresh database.
- [ ] Backend tests run the FastAPI app in-process (httpx `ASGITransport`) against an ephemeral testcontainers PostgreSQL with migrations applied and per-test reset (transaction rollback or truncate); one example test passes.
- [ ] Frontend unit runner (Vitest) and end-to-end runner (Playwright) are scaffolded, each with one passing example.
- [ ] Required environment variables are documented (names + purpose), including `APP_BASE_URL`.
