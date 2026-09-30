"""categorization_rules: Bookkeeper-authored rules that assign a target Account

Revision ID: 0007_categorization_rules
Revises: 0006_csv_import
Create Date: 2026-09-29

Ticket 09. One row per Categorization Rule per Book. Rules match payee/description
(contains/equals, case-insensitive) and amount/date (gte/lte/between) — conditions
AND-ed within a rule, OR expressed as a second rule — and evaluate first-match-wins
by ``priority`` to assign exactly one target Account.

``conditions`` is the JSON list ``[{field, operator, value, value2?}]`` (value2 only
for ``between``). ``target_qbo_account_id`` is the QBO Account id the rule assigns —
the same id space as ``accounts.qbo_id`` and ``imported_transactions.assigned_account_qbo_id``.
It is a plain text mirror, not an FK: QBO owns the Chart of Accounts and an account
can go inactive without the rule vanishing. ``invalid_target`` flags a rule whose
target is no longer an active categorization target; such a rule is kept but skipped
at match time (re-validated on rule save and on categorize-run).

The categorization columns on ``imported_transactions`` (assigned_account_qbo_id,
category_source, matched_rule_id) already exist from 0006, so this migration only
adds the rules table.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0007_categorization_rules"
down_revision: Union[str, None] = "0006_csv_import"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE categorization_rules (
            id                    uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            book_id               uuid        NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            priority              integer     NOT NULL,
            target_qbo_account_id text        NOT NULL,
            conditions            jsonb       NOT NULL,
            invalid_target        boolean     NOT NULL DEFAULT false,
            created_at            timestamptz NOT NULL DEFAULT now(),
            updated_at            timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    # First-match-wins reads the Book's rules in priority order — index that path.
    op.execute(
        "CREATE INDEX categorization_rules_book_priority_idx "
        "ON categorization_rules (book_id, priority)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS categorization_rules")
