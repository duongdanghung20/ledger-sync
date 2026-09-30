"""csv_import: Column-Mapping Profiles + de-duplicated Imported Transactions

Revision ID: 0006_csv_import
Revises: 0005_bank_accounts
Create Date: 2026-09-29

Ticket 08. The Bookkeeper's path from an arbitrary bank CSV to canonical,
de-duplicated Imported Transactions.

``column_mapping_profiles`` — one saved per-bank mapping per Book. ``config`` is
the JSON shape from the ticket (delimiter/decimal/date/amount/…); ``name`` and the
default ``bank_account_id`` are mirrored into columns for querying. Deleting a
Bank Account nulls the profile default (SET NULL); it does not delete the profile.

``imported_transactions`` — one canonical row per imported bank line, scoped to a
Bank Account. ``amount`` is ``numeric(14,2)`` (signed; negative = money out — matches
ticket 04), never a float. ``raw`` retains the source row (header->value) so nothing
is lost. ``dedup_key`` is the per-row key the multiset reconcile matches on
(``ext:<external_id>`` when the bank supplies a stable id, else ``content:<hash>`` over
date/amount/description/payee). A partial unique index enforces the bulletproof
external-id path at the DB level; the content path deliberately has NO unique
constraint, because two genuinely identical same-day transactions must both persist.

Categorization columns (``assigned_account_qbo_id``, ``category_source``,
``matched_rule_id``) are added now, nullable, so ticket 09 (rules/manual
categorization) needs no migration.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0006_csv_import"
down_revision: Union[str, None] = "0005_bank_accounts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE column_mapping_profiles (
            id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            book_id         uuid        NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            name            text        NOT NULL,
            bank_account_id uuid        REFERENCES bank_accounts(id) ON DELETE SET NULL,
            config          jsonb       NOT NULL,
            created_at      timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE imported_transactions (
            id                        uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
            book_id                   uuid          NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            bank_account_id           uuid          NOT NULL REFERENCES bank_accounts(id) ON DELETE CASCADE,
            column_mapping_profile_id uuid          REFERENCES column_mapping_profiles(id) ON DELETE SET NULL,
            date                      date          NOT NULL,
            amount                    numeric(14,2) NOT NULL,
            description               text          NOT NULL,
            payee                     text,
            external_id               text,
            imported_at               timestamptz   NOT NULL DEFAULT now(),
            raw                       jsonb          NOT NULL,
            dedup_key                 text          NOT NULL,
            -- filled by ticket 09 (categorization); nullable now so 09 needs no migration.
            assigned_account_qbo_id   text,
            category_source           text          NOT NULL DEFAULT 'none',
            matched_rule_id           uuid
        )
        """
    )
    # Dedup reconcile loads existing keys per Bank Account — index that read path.
    op.execute(
        "CREATE INDEX imported_transactions_bank_dedup_idx "
        "ON imported_transactions (bank_account_id, dedup_key)"
    )
    # Bulletproof external-id path: a bank-supplied unique id can appear at most
    # once per Bank Account. The content path (external_id NULL) is excluded, so
    # identical same-day transactions without an id are never blocked.
    op.execute(
        "CREATE UNIQUE INDEX imported_transactions_bank_external_id_uidx "
        "ON imported_transactions (bank_account_id, external_id) "
        "WHERE external_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS imported_transactions")
    op.execute("DROP TABLE IF EXISTS column_mapping_profiles")
