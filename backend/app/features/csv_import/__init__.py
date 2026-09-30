"""CSV import feature (ticket 08).

A Bookkeeper uploads any bank's CSV; a saved per-bank Column-Mapping Profile
(auto-suggested from the file's header signature) maps its columns to the
canonical fields, and rows import as de-duplicated Imported Transactions scoped
to a Bank Account.

Public seam for later tickets (import from ``app.features.csv_import``)::

    # Pure parse/dedup engine — no DB, table-driven testable (tickets 09/10 reuse):
    from app.features.csv_import import map_rows, reconcile, dedup_key, content_key

        map_rows(text, config) -> MapResult(good: list[ParsedRow], bad: list[RejectedRow])
        reconcile(incoming, existing_keys) -> DedupResult(to_import, duplicates,
                                                          in_file_duplicate_count)
        dedup_key(row) -> str        per-row key: "ext:<id>" or "content:<hash>"

    # Canonical imported rows (incl. bank_account_id, signed Decimal amount, date,
    # description, payee, and the categorization columns ticket 09 fills):
    from app.features.csv_import import list_imported_transactions

        list_imported_transactions(conn, book_id, *, bank_account_id=None) -> list[dict]
"""

from .engine import (
    DedupResult,
    MapResult,
    ParsedRow,
    RejectedRow,
    content_key,
    dedup_key,
    map_rows,
    parse_amount,
    parse_date,
    reconcile,
)
from .service import list_imported_transactions

__all__ = [
    "map_rows",
    "reconcile",
    "dedup_key",
    "content_key",
    "parse_amount",
    "parse_date",
    "list_imported_transactions",
    "ParsedRow",
    "RejectedRow",
    "MapResult",
    "DedupResult",
]
