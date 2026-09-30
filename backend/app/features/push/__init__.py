"""Idempotent push feature (ticket 11).

Pushes approved Journal Entries to QuickBooks so a retry after any failure never
double-posts: the entry's ``requestid`` (minted once at approval) makes QBO
replay a duplicate into the original, an ambiguous 5xx/NETWORK is resolved by
query-by-DocNumber instead of a blind re-post, and a per-Book advisory lock
serialises concurrent pushes.

Public seam for ticket 12 (import from here)::

    from app.features.push import push_book, retry_entry
        push_book(conn, book_id, client) -> {posted, failed, pending, throttled}
        retry_entry(conn, book_id, journal_entry_id, client) -> journal_entry_row

Sync status per entry is read via ticket 10's ``list_journal_entries``.
"""

from .service import push_book, retry_entry

__all__ = ["push_book", "retry_entry"]
