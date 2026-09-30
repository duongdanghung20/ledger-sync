"""accounts: read-only mirror of a Book's QuickBooks Chart of Accounts

Revision ID: 0004_accounts
Revises: 0003_qbo_connection
Create Date: 2026-09-29

Ticket 06. One row per QBO Account per Book, keyed by ``(book_id, qbo_id)`` so a
refresh upserts on ``qbo_id`` (rename/retype update in place; an account absent
from QuickBooks is flipped ``active = false`` rather than deleted — never a hard
delete — so historical references still resolve). The mirror keeps inactive
accounts. Also stamps ``books.last_synced_at`` (additive column) so a Book can
show when its CoA was last synced and the lazy refresh can tell if it's stale.
Hand-written, branching from the qbo_connection head.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0004_accounts"
down_revision: Union[str, None] = "0003_qbo_connection"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE accounts (
            id               uuid    PRIMARY KEY DEFAULT gen_random_uuid(),
            book_id          uuid    NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            qbo_id           text    NOT NULL,
            name             text    NOT NULL,
            account_type     text,
            account_sub_type text,
            classification   text,
            acct_num         text,
            active           boolean NOT NULL DEFAULT true,
            sync_token       text,
            parent_qbo_id    text,
            UNIQUE (book_id, qbo_id)
        )
        """
    )
    # Additive: when the Book's CoA was last reconciled (NULL = never synced,
    # which the lazy refresh treats as stale).
    op.execute("ALTER TABLE books ADD COLUMN last_synced_at timestamptz")


def downgrade() -> None:
    op.execute("ALTER TABLE books DROP COLUMN IF EXISTS last_synced_at")
    op.execute("DROP TABLE IF EXISTS accounts")
