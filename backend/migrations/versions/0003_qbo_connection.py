"""qbo_connection: one QuickBooks company connection per Book

Revision ID: 0003_qbo_connection
Revises: 0002_auth
Create Date: 2026-09-29

Ticket 05. One row per Book (``book_id`` is the PK -> 1:1 with a Book): the
realm, the current access token + its expiry, the refresh token **encrypted at
rest** (bytea; the key lives in ``TOKEN_ENC_KEY``, outside the DB), the OAuth
CSRF ``oauth_state`` during an in-flight authorization, and the connection
``status`` in {pending, connected, disconnected}. Hand-written, branching from
the auth head.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0003_qbo_connection"
down_revision: Union[str, None] = "0002_auth"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE quickbooks_connections (
            book_id                 uuid        PRIMARY KEY
                                        REFERENCES books(id) ON DELETE CASCADE,
            realm_id                text,
            access_token            text,
            access_token_expires_at timestamptz,
            refresh_token_encrypted bytea,
            status                  text        NOT NULL DEFAULT 'pending'
                                        CHECK (status IN ('pending', 'connected', 'disconnected')),
            oauth_state             text,
            connected_at            timestamptz,
            updated_at              timestamptz NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS quickbooks_connections")
