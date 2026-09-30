"""journal_entries: one balanced Journal Entry materialized per approved txn

Revision ID: 0008_journal_entries
Revises: 0007_categorization_rules
Create Date: 2026-09-29

Ticket 10. Approving a categorized Imported Transaction materializes exactly one
Journal Entry, 1:1 with the transaction (``imported_transaction_id`` UNIQUE). There
is NO lines table: the two debit/credit lines are reconstructed from the snapshot
(``category_account_qbo_id`` / ``cash_account_qbo_id`` / ``amount`` abs /
``debit_side``) by the pure sign rule, so the entry is immutable to later rule
re-runs or re-categorization.

``amount`` is ``numeric(14,2)`` and CHECKed ``> 0`` — the abs value both lines post;
a zero-amount transaction is rejected before it reaches here, so an empty/unbalanced
entry can never be stored. ``debit_side`` is CHECKed ``('category','cash')``.

``requestid`` (UUID) is minted at approval and reused forever (ticket 11's idempotent
QBO ``requestid``); ``doc_number`` is a short per-Book monotonic integer. The sync_*
columns (``sync_status`` default ``pending``, ``qbo_id``, ``posted_at``, ``last_error``,
``last_error_code``, ``last_attempt_at``, ``attempt_count``) exist now so ticket 11
(push) needs no migration.

``journal_doc_counters`` is the per-Book monotonic ``doc_number`` source: one row per
Book, incremented under an atomic ``INSERT ... ON CONFLICT DO UPDATE ... RETURNING``
(row-locked), so concurrent approvals never collide (also backstopped by the
``(book_id, doc_number)`` unique index).
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0008_journal_entries"
down_revision: Union[str, None] = "0007_categorization_rules"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE journal_doc_counters (
            book_id uuid    PRIMARY KEY REFERENCES books(id) ON DELETE CASCADE,
            value   integer NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE journal_entries (
            id                      uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
            book_id                 uuid          NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            imported_transaction_id uuid          NOT NULL UNIQUE
                                                  REFERENCES imported_transactions(id) ON DELETE CASCADE,
            -- snapshot: the entry is immutable to later re-categorization
            category_account_qbo_id text          NOT NULL,
            cash_account_qbo_id     text          NOT NULL,
            amount                  numeric(14,2) NOT NULL CHECK (amount > 0),
            debit_side              text          NOT NULL CHECK (debit_side IN ('category', 'cash')),
            memo                    text,
            -- sync fields (used by ticket 11; present now so 11 needs no migration)
            sync_status             text          NOT NULL DEFAULT 'pending'
                                                  CHECK (sync_status IN ('pending', 'posted', 'failed')),
            qbo_id                  text,
            requestid               uuid          NOT NULL,
            doc_number              integer       NOT NULL,
            posted_at               timestamptz,
            last_error              text,
            last_error_code         text,
            last_attempt_at         timestamptz,
            attempt_count           integer       NOT NULL DEFAULT 0,
            created_at              timestamptz   NOT NULL DEFAULT now(),
            UNIQUE (book_id, doc_number)
        )
        """
    )
    # Ticket 12's review surface lists a Book's entries newest-first.
    op.execute(
        "CREATE INDEX journal_entries_book_created_idx "
        "ON journal_entries (book_id, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS journal_entries")
    op.execute("DROP TABLE IF EXISTS journal_doc_counters")
