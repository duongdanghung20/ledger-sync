"""Categorization HTTP surface — Bookkeeper-allowed (guarded by ``current_user``).

Rules CRUD + run, plus the minimal manual assign/clear a Bookkeeper needs to
categorize an uncategorized transaction by hand (AC47). The dense transaction-review
table / Focus queue is ticket 12; it consumes the seam functions in ``service.py``.

- GET    /api/categorization/rules              rules (priority order) + valid targets
- POST   /api/categorization/rules              create a rule
- PATCH  /api/categorization/rules/{id}         edit priority / target / conditions
- DELETE /api/categorization/rules/{id}         delete a rule
- POST   /api/categorization/run                apply rules (manual overrides preserved)
- POST   /api/categorization/transactions/{id}/assign   manual override -> an account
- DELETE /api/categorization/transactions/{id}/category reset to uncategorized
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from psycopg import AsyncConnection
from pydantic import BaseModel

from app.db import get_conn
from app.features.accounts import categorization_targets
from app.features.auth.deps import current_user
from app.features.categorization import engine, service
from app.features.qbo_connection.service import book_id_for_org

router = APIRouter(prefix="/api/categorization", tags=["categorization"])


class RuleIn(BaseModel):
    priority: int
    target_qbo_account_id: str
    conditions: list


class RulePatch(BaseModel):
    priority: int | None = None
    target_qbo_account_id: str | None = None
    conditions: list | None = None


class AssignIn(BaseModel):
    qbo_account_id: str


def _rule_json(r: dict) -> dict:
    return {
        "id": str(r["id"]),
        "priority": r["priority"],
        "target_qbo_account_id": r["target_qbo_account_id"],
        "conditions": r["conditions"],
        "invalid_target": r["invalid_target"],
    }


async def _view(conn: AsyncConnection, book_id) -> dict:
    return {
        "rules": [_rule_json(r) for r in await service.list_rules(conn, book_id)],
        "targets": await categorization_targets(conn, book_id),
    }


async def _require_valid_target(conn: AsyncConnection, book_id, qbo_account_id: str) -> None:
    if qbo_account_id not in await service.valid_target_ids(conn, book_id):
        raise HTTPException(status_code=422, detail="Choose an active account for this book.")


# ------------------------------------------------------------------- rules ---


@router.get("/rules")
async def list_rules(
    user: dict = Depends(current_user), conn: AsyncConnection = Depends(get_conn)
) -> dict:
    book_id = await book_id_for_org(conn, user["organization_id"])
    return await _view(conn, book_id)


@router.post("/rules", status_code=201)
async def create_rule(
    body: RuleIn, user: dict = Depends(current_user), conn: AsyncConnection = Depends(get_conn)
) -> dict:
    book_id = await book_id_for_org(conn, user["organization_id"])
    try:
        await service.create_rule(
            conn, book_id, body.priority, body.target_qbo_account_id, body.conditions
        )
    except engine.RuleError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return await _view(conn, book_id)


@router.patch("/rules/{rule_id}")
async def update_rule(
    rule_id: UUID,
    body: RulePatch,
    user: dict = Depends(current_user),
    conn: AsyncConnection = Depends(get_conn),
) -> dict:
    book_id = await book_id_for_org(conn, user["organization_id"])
    try:
        updated = await service.update_rule(
            conn, book_id, rule_id,
            priority=body.priority,
            target_qbo_account_id=body.target_qbo_account_id,
            conditions=body.conditions,
        )
    except engine.RuleError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if updated is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    return await _view(conn, book_id)


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_rule(
    rule_id: UUID, user: dict = Depends(current_user), conn: AsyncConnection = Depends(get_conn)
) -> None:
    book_id = await book_id_for_org(conn, user["organization_id"])
    if not await service.delete_rule(conn, book_id, rule_id):
        raise HTTPException(status_code=404, detail="Rule not found")


# --------------------------------------------------------------- categorize ---


@router.post("/run")
async def run(
    user: dict = Depends(current_user), conn: AsyncConnection = Depends(get_conn)
) -> dict:
    book_id = await book_id_for_org(conn, user["organization_id"])
    return await service.run_categorization(conn, book_id)


@router.post("/transactions/{transaction_id}/assign")
async def assign(
    transaction_id: UUID,
    body: AssignIn,
    user: dict = Depends(current_user),
    conn: AsyncConnection = Depends(get_conn),
) -> dict:
    book_id = await book_id_for_org(conn, user["organization_id"])
    await _require_valid_target(conn, book_id, body.qbo_account_id)
    row = await service.set_manual_category(conn, book_id, transaction_id, body.qbo_account_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return {"id": str(row["id"]), "assigned_account_qbo_id": row["assigned_account_qbo_id"],
            "category_source": row["category_source"]}


@router.delete("/transactions/{transaction_id}/category")
async def clear(
    transaction_id: UUID,
    user: dict = Depends(current_user),
    conn: AsyncConnection = Depends(get_conn),
) -> dict:
    book_id = await book_id_for_org(conn, user["organization_id"])
    row = await service.clear_category(conn, book_id, transaction_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return {"id": str(row["id"]), "category_source": row["category_source"]}
