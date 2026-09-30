"""Launch the backend for the running-stack e2e: a throwaway Postgres, migrated
to head, then uvicorn with fake-QuickBooks mode on.

One command so the order is right (DB up -> migrate -> serve). Playwright starts
this as a webServer and waits for /api/health. Nothing here runs in production;
it is only invoked by the e2e.

    cd backend && .venv/bin/python e2e_server.py     # serves on 127.0.0.1:8000
"""

from __future__ import annotations

import os

import uvicorn
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from testcontainers.community.postgres import PostgresContainer

_ROOT = os.path.dirname(os.path.abspath(__file__))
_PORT = int(os.environ.get("E2E_BACKEND_PORT", "8000"))


def main() -> None:
    with PostgresContainer("postgres:16", driver=None) as pg:
        os.environ["DATABASE_URL"] = pg.get_connection_url()

        cfg = Config(os.path.join(_ROOT, "alembic.ini"))
        cfg.set_main_option("script_location", os.path.join(_ROOT, "migrations"))
        command.upgrade(cfg, "head")

        # Fake-QBO + bootstrap env (only defaults — override from the shell if set).
        os.environ.setdefault("QBO_FAKE", "1")
        os.environ.setdefault("TOKEN_ENC_KEY", Fernet.generate_key().decode())
        os.environ.setdefault("SESSION_SECRET", "e2e-session-secret")
        os.environ.setdefault("BOOTSTRAP_ADMIN_EMAIL", "admin@example.com")
        os.environ.setdefault("ORG_NAME", "Ledger-Sync E2E")
        os.environ.setdefault("APP_BASE_URL", f"http://127.0.0.1:{_PORT}")

        uvicorn.run("app.main:app", host="127.0.0.1", port=_PORT, log_level="warning")


if __name__ == "__main__":
    main()
