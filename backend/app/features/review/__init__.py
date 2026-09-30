"""Review read model (ticket 12).

The default review surface reads over the existing feature seams and never stores
a second status column: transaction state is DERIVED so it can't drift.

    from app.features.review import review_rows
        review_rows(conn, book_id) -> list[dict]   # one row per imported txn

Each row carries its derived ``state`` (uncategorized / categorized / approved /
posted / failed), the category assignment, and — once a Journal Entry exists — the
entry's sync fields (sync_status, last_error, last_error_code, attempt_count,
doc_number). The four write operations are NOT re-implemented here: the frontend
calls the existing endpoints (categorization assign/clear, journal approve/unapprove,
push/retry) directly. This package is pure read + one env-gated e2e fake (``e2e_fake``).
"""

from .service import review_rows

__all__ = ["review_rows"]
