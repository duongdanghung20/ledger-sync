"""CSV import HTTP surface — Bookkeeper-allowed (guarded by ``current_user``).

Flow: the Bookkeeper uploads a CSV to ``/preview`` (no writes) which suggests the
matching profile from the file's header signature and returns the saved profiles
and the Book's Bank Accounts; they confirm the profile + source Bank Account and
POST the file to ``/`` to import. Bank Account **creation stays Admin-only**
(ticket 07): the Bookkeeper picks an existing one, and the UI links to the Admin
screen when none exists.

The file is carried as the CSV *text* in a JSON body (the client reads it with
``File.text()``), not multipart/form-data: this stack does not ship
``python-multipart`` and ticket 08 must add no dependency, so ``UploadFile``/``Form``
are unavailable — FastAPI parses the JSON body natively instead.

- GET    /api/csv-import/profiles        saved profiles + the Book's bank accounts
- POST   /api/csv-import/profiles        create a Column-Mapping Profile
- PATCH  /api/csv-import/profiles/{id}   rename / re-map / re-default
- DELETE /api/csv-import/profiles/{id}   delete a profile
- POST   /api/csv-import/preview         suggest a profile for an uploaded file
- POST   /api/csv-import                 import an uploaded file (map -> dedup -> insert)
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from psycopg import AsyncConnection
from pydantic import BaseModel, field_validator

from app.db import get_conn
from app.features.auth.deps import current_user
from app.features.bank_accounts import get_bank_account, list_bank_accounts
from app.features.csv_import import engine, service
from app.features.qbo_connection.service import book_id_for_org

router = APIRouter(prefix="/api/csv-import", tags=["csv_import"])


class ProfileIn(BaseModel):
    name: str
    bank_account_id: UUID | None = None
    config: dict

    @field_validator("name")
    @classmethod
    def _required(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Name this profile")
        return v


class ProfilePatch(BaseModel):
    name: str | None = None
    bank_account_id: UUID | None = None
    config: dict | None = None

    @field_validator("name")
    @classmethod
    def _non_blank(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("Name cannot be blank")
        return v.strip() if v is not None else v


def _profile_json(p: dict) -> dict:
    return {
        "id": str(p["id"]),
        "name": p["name"],
        "bank_account_id": str(p["bank_account_id"]) if p["bank_account_id"] else None,
        "config": p["config"],
    }


def _bank_account_json(b: dict) -> dict:
    return {"id": str(b["id"]), "name": b["name"], "qbo_account_id": b["qbo_account_id"]}


async def _view(conn: AsyncConnection, book_id) -> dict:
    profiles = [_profile_json(p) for p in await service.list_profiles(conn, book_id)]
    banks = [_bank_account_json(b) for b in await list_bank_accounts(conn, book_id)]
    return {"profiles": profiles, "bank_accounts": banks}


async def _require_bank_account(conn: AsyncConnection, book_id, bank_account_id) -> dict:
    """The chosen Bank Account must exist and belong to this Book. Creation is
    Admin-only (ticket 07) — never created here."""
    ba = await get_bank_account(conn, bank_account_id)
    if ba is None or ba["book_id"] != book_id:
        raise HTTPException(status_code=422, detail="Choose an existing bank account for this book.")
    return ba


async def _require_profile(conn: AsyncConnection, book_id, profile_id) -> dict:
    p = await service.get_profile(conn, book_id, profile_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Column-mapping profile not found")
    return p


class PreviewIn(BaseModel):
    csv: str


class ImportIn(BaseModel):
    csv: str
    profile_id: UUID
    bank_account_id: UUID


# --------------------------------------------------------------- profiles ----


@router.get("/profiles")
async def list_profiles(
    user: dict = Depends(current_user),
    conn: AsyncConnection = Depends(get_conn),
) -> dict:
    book_id = await book_id_for_org(conn, user["organization_id"])
    return await _view(conn, book_id)


@router.post("/profiles", status_code=201)
async def create_profile(
    body: ProfileIn,
    user: dict = Depends(current_user),
    conn: AsyncConnection = Depends(get_conn),
) -> dict:
    book_id = await book_id_for_org(conn, user["organization_id"])
    if body.bank_account_id is not None:
        await _require_bank_account(conn, book_id, body.bank_account_id)
    try:
        await service.create_profile(conn, book_id, body.name, body.config, body.bank_account_id)
    except engine.ConfigError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return await _view(conn, book_id)


@router.patch("/profiles/{profile_id}")
async def update_profile(
    profile_id: UUID,
    body: ProfilePatch,
    user: dict = Depends(current_user),
    conn: AsyncConnection = Depends(get_conn),
) -> dict:
    book_id = await book_id_for_org(conn, user["organization_id"])
    await _require_profile(conn, book_id, profile_id)
    if body.bank_account_id is not None:
        await _require_bank_account(conn, book_id, body.bank_account_id)
    # A PATCH that names bank_account_id: null clears it; omitting it leaves it.
    bank = body.bank_account_id if "bank_account_id" in body.model_fields_set else service._UNSET
    try:
        await service.update_profile(
            conn, book_id, profile_id, name=body.name, config=body.config, bank_account_id=bank
        )
    except engine.ConfigError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return await _view(conn, book_id)


@router.delete("/profiles/{profile_id}", status_code=204)
async def delete_profile(
    profile_id: UUID,
    user: dict = Depends(current_user),
    conn: AsyncConnection = Depends(get_conn),
) -> None:
    book_id = await book_id_for_org(conn, user["organization_id"])
    if not await service.delete_profile(conn, book_id, profile_id):
        raise HTTPException(status_code=404, detail="Column-mapping profile not found")


# ----------------------------------------------------------- upload flow -----


@router.post("/preview")
async def preview(
    body: PreviewIn,
    user: dict = Depends(current_user),
    conn: AsyncConnection = Depends(get_conn),
) -> dict:
    """Suggest the matching profile for an uploaded file's header signature. No
    writes — the Bookkeeper confirms before importing."""
    book_id = await book_id_for_org(conn, user["organization_id"])
    profiles = await service.list_profiles(conn, book_id)

    # Read the header with each candidate delimiter so suggestion works before a
    # profile is chosen; the eventual import re-parses with the confirmed profile.
    header: list[str] = []
    for delim in (",", ";", "\t", "|"):
        rows = engine._read_rows(body.csv, delim)
        row = rows[0] if rows else []
        if len(row) > len(header):
            header = [h.strip() for h in row]

    suggested = service.suggest_profile(profiles, header)
    view = await _view(conn, book_id)
    view["header"] = header
    view["suggested_profile_id"] = str(suggested["id"]) if suggested else None
    return view


@router.post("")
async def import_csv(
    body: ImportIn,
    user: dict = Depends(current_user),
    conn: AsyncConnection = Depends(get_conn),
) -> dict:
    """Import the uploaded file with the confirmed profile + source Bank Account:
    map -> dedup (per Bank Account) -> insert, all in the request transaction."""
    book_id = await book_id_for_org(conn, user["organization_id"])
    profile = await _require_profile(conn, book_id, body.profile_id)
    await _require_bank_account(conn, book_id, body.bank_account_id)
    try:
        return await service.import_csv(conn, book_id, body.bank_account_id, profile, body.csv)
    except engine.ConfigError as e:
        raise HTTPException(status_code=422, detail=str(e))
