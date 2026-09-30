"""Chart-of-Accounts mirror feature (ticket 06).

Read-only mirror of a Book's QuickBooks Chart of Accounts. QBO owns the CoA;
Ledger-Sync only reads it down. Refresh reconciles by ``qbo_id`` (rename/retype
in place, absent -> inactive, never a hard delete) and keeps inactive accounts.

Public seam for later tickets (import from here)::

    from app.features.accounts import (
        reconcile,               # reconcile(conn, book_id, qbo_accounts) -> dict
        refresh,                 # refresh(conn, book_id, client) -> QboResult
        refresh_if_stale,        # refresh_if_stale(conn, book_id, client, *, max_age=15m) -> bool
        categorization_targets,  # categorization_targets(conn, book_id) -> dict[classification, [account]]
        last_synced_at,          # last_synced_at(conn, book_id) -> datetime | None
    )

Tickets 09/12 (review/categorize) call ``refresh_if_stale`` before reading and
``categorization_targets`` to offer accounts; the push path calls
``refresh_if_stale`` so a stale mirror never corrupts a post.
"""

from .service import (
    categorization_targets,
    last_synced_at,
    list_accounts,
    reconcile,
    refresh,
    refresh_if_stale,
)

__all__ = [
    "reconcile",
    "refresh",
    "refresh_if_stale",
    "categorization_targets",
    "last_synced_at",
    "list_accounts",
]
