"""baseline

Revision ID: 0001_baseline
Revises:
Create Date: 2026-09-29

Schema baseline. Enables the ``pgcrypto`` extension so later revisions can use
``gen_random_uuid()`` for UUID primary keys. Feature tickets add their own
revision file (``alembic revision -m "..."``) branching from a current head;
parallel branches are reconciled once with ``alembic merge heads``.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS pgcrypto")
