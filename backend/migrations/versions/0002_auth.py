"""auth: organizations, books, users, sessions, invitations

Revision ID: 0002_auth
Revises: 0001_baseline
Create Date: 2026-09-29

Schema for bootstrap + login + server-side sessions (ticket 02). Hand-written,
branching from the baseline (which enabled ``pgcrypto`` -> ``gen_random_uuid()``).
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0002_auth"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE organizations (
            id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            name       text        NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE books (
            id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid        NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            name            text        NOT NULL,
            created_at      timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE users (
            id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id   uuid        NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            email             text        NOT NULL,
            password_hash     text,
            role              text        NOT NULL CHECK (role IN ('Admin', 'Bookkeeper')),
            disabled          boolean     NOT NULL DEFAULT false,
            must_set_password boolean     NOT NULL DEFAULT true,
            created_at        timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    # Case-insensitive email uniqueness.
    op.execute("CREATE UNIQUE INDEX users_email_lower_key ON users (lower(email))")
    op.execute(
        """
        CREATE TABLE sessions (
            id         text        PRIMARY KEY,
            user_id    uuid        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX sessions_user_id_idx ON sessions (user_id)")
    op.execute(
        """
        CREATE TABLE invitations (
            id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id     uuid        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token       text        NOT NULL UNIQUE,
            created_at  timestamptz NOT NULL DEFAULT now(),
            expires_at  timestamptz NOT NULL,
            redeemed_at timestamptz
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS invitations")
    op.execute("DROP TABLE IF EXISTS sessions")
    op.execute("DROP TABLE IF EXISTS users")
    op.execute("DROP TABLE IF EXISTS books")
    op.execute("DROP TABLE IF EXISTS organizations")
