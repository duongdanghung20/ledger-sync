"""Pure CSV parse + multiset dedup engine — lifted from the ticket-05 prototype.

No DB, no framework, no I/O: every function here takes plain data and returns
plain data, so it is table-driven testable and reused by tickets 09/10. The
prototype's validated logic (dedup corpus passes 7/7) is the authoritative
source; this is that logic in Python stdlib (`csv`, `decimal`).

Canonical fields (matches ticket 04): ``date`` (a ``date``), ``amount`` (a signed
2dp ``Decimal`` — **negative = money out** of the Bank Account), ``description``,
optional ``payee``, optional ``external_id`` (bank-provided stable row id).

Public seam::

    from app.features.csv_import.engine import map_rows, reconcile, dedup_key

    map_rows(text, config) -> MapResult(good: list[ParsedRow], bad: list[RejectedRow])
        Parse a raw CSV against a Column-Mapping Profile config. Valid rows land in
        ``good`` as ParsedRow; malformed/partial rows land in ``bad`` with reasons.

    reconcile(incoming, existing_keys) -> DedupResult(to_import, duplicates, in_file_duplicate_count)
        Per-Bank-Account dedup. ``existing_keys`` are the dedup_keys already imported
        for that Bank Account. External-id rows dedup with certainty (never repeat);
        content rows reconcile as a multiset (identical same-day rows both kept, only
        an already-imported one is skipped). In-file content collisions are counted
        for review, never silently dropped.

    dedup_key(row) -> str          the per-row key stored on the imported row.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

_TWOPLACES = Decimal("0.01")
# Profile date-format tokens -> strptime patterns. strptime validates real dates
# (rejects 02/30, month 13, …), so it is stricter and more correct than the
# prototype's loose range check.
_DATE_FORMATS = {
    "YYYY-MM-DD": "%Y-%m-%d",
    "MM/DD/YYYY": "%m/%d/%Y",
    "DD/MM/YYYY": "%d/%m/%Y",
}


@dataclass(frozen=True)
class ParsedRow:
    date: date
    amount: Decimal
    description: str
    payee: str
    external_id: str
    raw: dict = field(default_factory=dict, compare=False)


@dataclass
class RejectedRow:
    raw: dict
    reasons: list[str]


@dataclass
class MapResult:
    good: list[ParsedRow]
    bad: list[RejectedRow]


@dataclass
class DedupResult:
    to_import: list[ParsedRow]
    duplicates: list[ParsedRow]
    in_file_duplicate_count: int


class ConfigError(ValueError):
    """A Column-Mapping Profile config is missing or has an invalid field."""


# ---------------------------------------------------------------- parsing ----


def parse_amount(raw, decimal_style: str) -> Decimal | None:
    """Parse one amount cell to a signed 2dp Decimal, or None if unparseable.

    Handles ``(1,234.56)`` parentheses-negative, currency symbols/spaces, and both
    decimal conventions: ``dot`` (1,234.56) and ``comma`` (1.234,56)."""
    s = "" if raw is None else str(raw).strip()
    if s == "":
        return None
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1]
    s = re.sub(r"[^\d.,\-]", "", s)  # strip currency symbols / spaces
    if decimal_style == "comma":
        s = s.replace(".", "").replace(",", ".")  # 3.200,00 -> 3200.00
    else:
        s = s.replace(",", "")  # 1,234.56 -> 1234.56
    try:
        n = Decimal(s)
    except InvalidOperation:
        return None
    if neg:
        n = -abs(n)
    return n.quantize(_TWOPLACES, rounding=ROUND_HALF_UP)


def parse_date(raw, fmt: str) -> date | None:
    """Parse one date cell by the profile's explicit format, or None. Tolerates a
    trailing time component (``2026-09-01T00:00:00`` / ``09/01/2026 12:00``)."""
    s = "" if raw is None else str(raw).strip()
    pattern = _DATE_FORMATS.get(fmt)
    if not pattern or not s:
        return None
    token = s.split()[0].split("T")[0]  # date formats here never contain a space
    try:
        return datetime.strptime(token, pattern).date()
    except ValueError:
        return None


def _col(cfg: dict, key: str):
    """A profile field's mapped column name (``{"column": "..."}``), or None."""
    spec = cfg.get(key)
    return spec.get("column") if isinstance(spec, dict) else None


def validate_config(cfg: dict) -> None:
    """Reject a profile config that can't drive an import. Raised as 422 upstream."""
    if not isinstance(cfg, dict):
        raise ConfigError("Profile config must be an object")
    if cfg.get("decimal", "dot") not in ("dot", "comma"):
        raise ConfigError("decimal must be 'dot' or 'comma'")
    if not _col(cfg, "date"):
        raise ConfigError("date.column is required")
    date_spec = cfg.get("date") or {}
    if date_spec.get("format") not in _DATE_FORMATS:
        raise ConfigError(f"date.format must be one of {sorted(_DATE_FORMATS)}")
    if not _col(cfg, "description"):
        raise ConfigError("description.column is required")
    amount = cfg.get("amount")
    if not isinstance(amount, dict):
        raise ConfigError("amount is required")
    mode = amount.get("mode")
    if mode == "signed":
        if not amount.get("column"):
            raise ConfigError("amount.column is required for a signed amount")
    elif mode == "debit_credit":
        if not amount.get("debit_column") or not amount.get("credit_column"):
            raise ConfigError("amount.debit_column and amount.credit_column are required")
    else:
        raise ConfigError("amount.mode must be 'signed' or 'debit_credit'")


def _read_rows(text: str, delimiter: str) -> list[list[str]]:
    """RFC4180 rows via stdlib csv (quotes, embedded delimiters/newlines, CRLF).
    Strips a BOM and drops fully-blank lines, matching the prototype."""
    text = text.lstrip("﻿")
    reader = csv.reader(io.StringIO(text), delimiter=delimiter or ",")
    return [r for r in reader if any(cell.strip() for cell in r)]


def _amount_for(cfg: dict, get, decimal_style: str) -> Decimal | None:
    amount = cfg["amount"]
    if amount.get("mode") == "signed":
        n = parse_amount(get(amount.get("column")), decimal_style)
        if n is not None and amount.get("flip"):
            n = -n
        return n
    # debit_credit: debit = money out (negative), credit = money in (positive).
    debit = parse_amount(get(amount.get("debit_column")), decimal_style)
    if debit is not None:
        return -abs(debit)
    credit = parse_amount(get(amount.get("credit_column")), decimal_style)
    if credit is not None:
        return abs(credit)
    return None


def map_rows(text: str, cfg: dict) -> MapResult:
    """Map a raw CSV to canonical ParsedRows against a profile config, collecting
    malformed/partial rows (with reasons) rather than dropping them."""
    validate_config(cfg)
    rows = _read_rows(text, cfg.get("delimiter", ","))
    header_row = cfg.get("header_row", 0) or 0
    if header_row >= len(rows):
        return MapResult([], [])
    header = [h.strip() for h in rows[header_row]]
    index = {name: i for i, name in enumerate(header)}
    skip = cfg.get("skip_trailing", 0) or 0
    end = len(rows) - skip if skip else len(rows)
    body = rows[header_row + 1 : end]
    decimal_style = cfg.get("decimal", "dot")

    good: list[ParsedRow] = []
    bad: list[RejectedRow] = []
    for r in body:
        def get(name):
            i = index.get(name)
            return r[i] if i is not None and i < len(r) else None

        raw = {h: (r[i] if i < len(r) else "") for i, h in enumerate(header)}
        the_date = parse_date(get(_col(cfg, "date")), (cfg.get("date") or {}).get("format"))
        amount = _amount_for(cfg, get, decimal_style)
        description = (get(_col(cfg, "description")) or "").strip()
        payee = (get(_col(cfg, "payee")) or "").strip() if _col(cfg, "payee") else ""
        external_id = (
            (get(_col(cfg, "external_id")) or "").strip() if _col(cfg, "external_id") else ""
        )

        reasons = []
        if the_date is None:
            reasons.append("unparseable date")
        if amount is None:
            reasons.append("no/invalid amount")
        if not description:
            reasons.append("empty description")
        if reasons:
            bad.append(RejectedRow(raw=raw, reasons=reasons))
        else:
            good.append(
                ParsedRow(
                    date=the_date,
                    amount=amount,
                    description=description,
                    payee=payee,
                    external_id=external_id,
                    raw=raw,
                )
            )
    return MapResult(good=good, bad=bad)


# ----------------------------------------------------------------- dedup -----


def content_key(the_date: date, amount: Decimal, description: str, payee: str) -> str:
    """Stable hash over (date, signed amount, description, payee), case-folded on
    the text — the content dedup key when the bank supplies no unique id."""
    parts = "\x01".join(
        [the_date.isoformat(), f"{amount:.2f}", (description or "").lower(), (payee or "").lower()]
    )
    return hashlib.sha256(parts.encode("utf-8")).hexdigest()


def dedup_key(row: ParsedRow) -> str:
    """The per-row key stored on the imported row. Prefixed so an external key and
    a content key can never collide."""
    if row.external_id:
        return "ext:" + row.external_id
    return "content:" + content_key(row.date, row.amount, row.description, row.payee)


def reconcile(incoming: list[ParsedRow], existing_keys: list[str]) -> DedupResult:
    """Per-Bank-Account multiset dedup against already-imported ``existing_keys``.

    - external-id rows: dedup with certainty — an id already imported (or already
      seen earlier in this same file) is a duplicate; every distinct id is kept.
    - content rows: reconcile as a multiset — an incoming row is a duplicate only
      while an already-imported row with the same key is still unmatched; extras
      beyond the existing multiplicity are genuinely new (two identical same-day
      transactions are both kept on a first import).

    In-file content collisions among the kept rows are surfaced as a count for the
    Bookkeeper to review, never silently decided.
    """
    avail = Counter(k for k in existing_keys if k.startswith("content:"))
    seen_ext = {k for k in existing_keys if k.startswith("ext:")}
    to_import: list[ParsedRow] = []
    duplicates: list[ParsedRow] = []
    for row in incoming:
        k = dedup_key(row)
        if k.startswith("ext:"):
            if k in seen_ext:
                duplicates.append(row)
            else:
                seen_ext.add(k)
                to_import.append(row)
        else:
            if avail[k] > 0:
                avail[k] -= 1
                duplicates.append(row)
            else:
                to_import.append(row)
    # Possible in-file duplicates: content rows kept that share a key with another
    # kept row. External rows are certain, so they never count here.
    kept = Counter(dedup_key(r) for r in to_import if not r.external_id)
    in_file_duplicate_count = sum(c - 1 for c in kept.values() if c > 1)
    return DedupResult(
        to_import=to_import,
        duplicates=duplicates,
        in_file_duplicate_count=in_file_duplicate_count,
    )
