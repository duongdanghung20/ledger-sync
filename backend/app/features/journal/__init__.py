"""Journal-entry materialization feature (ticket 10).

Approving a categorized Imported Transaction materializes exactly one balanced
two-line Journal Entry, correct by construction (the sign rule posts ``abs(amount)``
on both lines with opposite ``PostingType``, so debits == credits and QBO error 2300
is impossible). There is no lines table: the two lines are reconstructed from a
stored snapshot, which makes the entry immutable to later rule re-runs.

Public seam for tickets 11 (push) / 12 (review) — import from here::

    # Pure sign rule + mappings (no DB):
    from app.features.journal import build_lines, preview, to_port_entry
        build_lines(signed_amount: Decimal, category_qbo_id, cash_qbo_id) -> (debit, credit)
        preview(txn_row) -> (debit, credit)    # row carries cash_account_qbo_id
        to_port_entry(je_row) -> qbo.Entry     # ticket 11 pushes this

    # DB operations:
    from app.features.journal import approve, unapprove, \
        list_journal_entries, get_journal_entry
        approve(conn, book_id, transaction_id) -> journal_entry_row (dict)
        unapprove(conn, book_id, journal_entry_id) -> None
        list_journal_entries(conn, book_id) -> list[dict]           # ticket 12
        get_journal_entry(conn, book_id, journal_entry_id) -> dict | None  # ticket 11
        service.preview_transaction(conn, book_id, transaction_id)  # DB wrapper of preview

Errors are ``engine.JournalError`` subclasses carrying an HTTP ``status_code``:
``ZeroAmount``, ``Uncategorized``, ``UnmappedBankAccount``, ``AlreadyApproved``,
``NotPending``, ``NotFound``.
"""

from .engine import (
    AlreadyApproved,
    JournalError,
    NotFound,
    NotPending,
    Uncategorized,
    UnmappedBankAccount,
    ZeroAmount,
    build_lines,
    preview,
    to_port_entry,
)
from .service import (
    approve,
    get_journal_entry,
    list_journal_entries,
    unapprove,
)

__all__ = [
    "build_lines",
    "preview",
    "to_port_entry",
    "approve",
    "unapprove",
    "list_journal_entries",
    "get_journal_entry",
    "JournalError",
    "ZeroAmount",
    "Uncategorized",
    "UnmappedBankAccount",
    "AlreadyApproved",
    "NotPending",
    "NotFound",
]
