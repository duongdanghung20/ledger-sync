"""Alembic environment.

Reads the database URL from DATABASE_URL. The app talks to PostgreSQL with raw
psycopg 3 (a plain ``postgresql://`` URL); SQLAlchemy — which Alembic uses to
run migrations — needs the psycopg-3 dialect named explicitly, so we rewrite the
scheme here. That keeps a single driver (psycopg 3) in the stack.

Migrations are hand-written (raw SQL / Alembic ops), so there is no ORM metadata
to autogenerate against — ``target_metadata`` stays ``None``.
"""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None


def _database_url() -> str:
    url = os.environ["DATABASE_URL"]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
