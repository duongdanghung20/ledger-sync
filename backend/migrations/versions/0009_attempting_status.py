"""journal_entries.sync_status: add the 'attempting' state

Revision ID: 0009_attempting_status
Revises: 0008_journal_entries
Create Date: 2026-09-29

Money-path fix (journal/push seam). ``push`` now commits ``sync_status='attempting'``
in its own transaction BEFORE the QBO POST, so a process death between the POST and
the result commit leaves a durable marker (instead of rolling back to ``pending`` and
letting an unapprove → re-approve mint a fresh ``requestid`` that double-posts). The
CHECK constraint has to allow the new value.

The constraint was created inline in 0008, so PostgreSQL auto-named it
``journal_entries_sync_status_check``. Drop + recreate it to widen the allowed set.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0009_attempting_status"
down_revision: Union[str, None] = "0008_journal_entries"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CONSTRAINT = "journal_entries_sync_status_check"


def upgrade() -> None:
    op.execute(f"ALTER TABLE journal_entries DROP CONSTRAINT {_CONSTRAINT}")
    op.execute(
        f"ALTER TABLE journal_entries ADD CONSTRAINT {_CONSTRAINT} "
        f"CHECK (sync_status IN ('pending', 'attempting', 'posted', 'failed'))"
    )


def downgrade() -> None:
    op.execute(f"ALTER TABLE journal_entries DROP CONSTRAINT {_CONSTRAINT}")
    op.execute(
        f"ALTER TABLE journal_entries ADD CONSTRAINT {_CONSTRAINT} "
        f"CHECK (sync_status IN ('pending', 'posted', 'failed'))"
    )
