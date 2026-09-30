"""bank_accounts: an Admin's real-world Bank Account mapped 1:1 to a cash-side Account

Revision ID: 0005_bank_accounts
Revises: 0004_accounts
Create Date: 2026-09-29

Ticket 07. One row per real-world Bank Account (checking, card) in a Book, each
carrying the ``qbo_account_id`` of the single Chart-of-Accounts Account it maps
to (its cash side). The mapping is required (``qbo_account_id`` NOT NULL), so
every Journal Entry later built from this Bank Account's transactions has a
defined counter-side.

``qbo_account_id`` is a plain stored value (the mirror's ``accounts.qbo_id``),
deliberately NOT a foreign key to ``accounts``: the ticket-06 mirror soft-
deactivates an Account (never a hard delete) when it leaves QuickBooks, and
tickets 10/11 treat a Bank Account whose ``qbo_account_id`` no longer resolves to
an *active* mirror Account as "unmapped". A cross-table FK would instead block
that deactivation, so the validity check lives in the mirror lookup, not a
constraint. Hand-written, branching from the accounts head.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0005_bank_accounts"
down_revision: Union[str, None] = "0004_accounts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE bank_accounts (
            id             uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            book_id        uuid        NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            name           text        NOT NULL,
            qbo_account_id text        NOT NULL,
            created_at     timestamptz NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS bank_accounts")
