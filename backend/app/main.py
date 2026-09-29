"""FastAPI application entrypoint.

Feature routers self-register: a feature is a subpackage of `app.features` that
exposes `router` (an `APIRouter`) in its `router` module —
`app/features/<name>/router.py`. Adding a feature never edits this file.
"""

import importlib
import importlib.util
import pkgutil
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from psycopg_pool import AsyncConnectionPool

from app import config, features


def _include_feature_routers(app: FastAPI) -> None:
    for mod in pkgutil.iter_modules(features.__path__, features.__name__ + "."):
        router_module = f"{mod.name}.router"
        if importlib.util.find_spec(router_module) is None:
            continue
        router = getattr(importlib.import_module(router_module), "router", None)
        if isinstance(router, APIRouter):
            app.include_router(router)


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = AsyncConnectionPool(config.env("DATABASE_URL"), open=False)
    await pool.open()
    app.state.pool = pool
    try:
        yield
    finally:
        await pool.close()


def create_app() -> FastAPI:
    app = FastAPI(title="Ledger-Sync", lifespan=lifespan)
    _include_feature_routers(app)
    return app


app = create_app()
