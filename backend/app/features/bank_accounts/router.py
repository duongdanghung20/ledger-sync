"""Bank Accounts HTTP surface — Admin-only.

An Admin creates each real-world Bank Account and maps it 1:1 to an active
cash-side Account from the Chart-of-Accounts mirror (ticket 06), and re-maps it
later. Every route is guarded by ``require_role("Admin")`` so a Bookkeeper (or a
tampered frontend) is rejected server-side; each is scoped to the caller's own
Book. The setup screen reads GET, which also returns the active mirror accounts
the create/map picker offers, plus each Bank Account's resolved mapped Account.

- GET   /api/bank-accounts        bank accounts (+ mapped account) + active picker options
- POST  /api/bank-accounts        create + map (422 if the target isn't an active mirror account)
- PATCH /api/bank-accounts/{id}   re-map and/or rename
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from psycopg import AsyncConnection
from pydantic import BaseModel, field_validator, model_validator

from app.db import get_conn
from app.features.accounts import list_accounts
from app.features.auth.deps import require_role
from app.features.bank_accounts import service
from app.features.qbo_connection.service import book_id_for_org

router = APIRouter(prefix="/api/bank-accounts", tags=["bank_accounts"])


class CreateIn(BaseModel):
    name: str
    qbo_account_id: str

    @field_validator("name", "qbo_account_id")
    @classmethod
    def _required(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("This field is required")
        return v


class UpdateIn(BaseModel):
    name: str | None = None
    qbo_account_id: str | None = None

    @field_validator("name", "qbo_account_id")
    @classmethod
    def _non_blank(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("Cannot be blank")
        return v

    @model_validator(mode="after")
    def _something_to_do(self):
        if self.name is None and self.qbo_account_id is None:
            raise ValueError("Provide a new name or a new mapped account")
        return self


async def _view(conn: AsyncConnection, book_id) -> dict:
    """The setup-screen payload: each Bank Account with its resolved mapped
    Account, plus the active mirror accounts the picker offers. Loads the mirror
    once and joins in memory (qbo_account_id is a plain value, not a FK)."""
    mirror = {a["qbo_id"]: a for a in await list_accounts(conn, book_id)}
    bank_accounts = []
    for r in await service.list_bank_accounts(conn, book_id):
        acct = mirror.get(r["qbo_account_id"])
        bank_accounts.append(
            {
                "id": str(r["id"]),
                "name": r["name"],
                "qbo_account_id": r["qbo_account_id"],
                # None when the mapped Account no longer resolves; active=false
                # when it was deactivated in QuickBooks (tickets 10/11: "unmapped").
                "mapped_account": (
                    {"qbo_id": acct["qbo_id"], "name": acct["name"], "active": acct["active"]}
                    if acct
                    else None
                ),
            }
        )
    accounts = [
        {
            "qbo_id": a["qbo_id"],
            "name": a["name"],
            "account_type": a["account_type"],
            "acct_num": a["acct_num"],
        }
        for a in mirror.values()
        if a["active"]
    ]
    return {"bank_accounts": bank_accounts, "accounts": accounts}


@router.get("")
async def list_bank_accounts(
    admin: dict = Depends(require_role("Admin")),
    conn: AsyncConnection = Depends(get_conn),
) -> dict:
    book_id = await book_id_for_org(conn, admin["organization_id"])
    return await _view(conn, book_id)


@router.post("", status_code=201)
async def create_bank_account(
    body: CreateIn,
    admin: dict = Depends(require_role("Admin")),
    conn: AsyncConnection = Depends(get_conn),
) -> dict:
    book_id = await book_id_for_org(conn, admin["organization_id"])
    await service.create_bank_account(conn, book_id, body.name, body.qbo_account_id)
    return await _view(conn, book_id)


@router.patch("/{bank_account_id}")
async def remap_bank_account(
    bank_account_id: UUID,
    body: UpdateIn,
    admin: dict = Depends(require_role("Admin")),
    conn: AsyncConnection = Depends(get_conn),
) -> dict:
    book_id = await book_id_for_org(conn, admin["organization_id"])
    updated = await service.remap_bank_account(
        conn, book_id, bank_account_id, name=body.name, qbo_account_id=body.qbo_account_id
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Bank account not found")
    return await _view(conn, book_id)
