"""Review HTTP surface — the read model behind ticket 12's Ledger table.

One GET returns every imported transaction with its DERIVED state, category
assignment, the categorization targets to pick from, and (once approved) the
Journal Entry's sync fields. The four write operations are NOT here — the review
surface reuses the existing endpoints:

    assign / clear   POST/DELETE /api/categorization/transactions/{id}/...
    approve          POST        /api/journal/transactions/{id}/approve
    un-approve       POST        /api/journal/entries/{id}/unapprove
    push / retry     POST        /api/push , /api/push/entries/{id}/retry

Bookkeeper-allowed (``current_user``). The env-gated fake-QBO lifespan is a no-op
unless ``QBO_FAKE`` is set (see ``e2e_fake``).

- GET /api/review   { transactions: [row], targets: {classification: [account]} }
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from psycopg import AsyncConnection

from app.db import get_conn
from app.features.accounts import categorization_targets
from app.features.auth.deps import current_user
from app.features.qbo_connection.service import book_id_for_org
from app.features.review import service
from app.features.review.e2e_fake import fake_qbo_lifespan

router = APIRouter(prefix="/api/review", tags=["review"], lifespan=fake_qbo_lifespan)


@router.get("")
async def review(
    user: dict = Depends(current_user), conn: AsyncConnection = Depends(get_conn)
) -> dict:
    book_id = await book_id_for_org(conn, user["organization_id"])
    return {
        "transactions": await service.review_rows(conn, book_id),
        "targets": await categorization_targets(conn, book_id),
    }
