"""Bank Accounts feature (ticket 07).

An Admin creates each real-world Bank Account (checking, card) in a Book and maps
it one-to-one to the single active QuickBooks Account (its cash side), chosen
from the ticket-06 Chart-of-Accounts mirror. The mapping (``qbo_account_id``) is
required, so every Journal Entry later built from that Bank Account's
transactions has a defined counter-side.

Public seam for later tickets (import from ``app.features.bank_accounts``)::

    from app.features.bank_accounts import list_bank_accounts, get_bank_account

    list_bank_accounts(conn, book_id) -> list[dict]
        Every Bank Account in the Book, each row incl. ``qbo_account_id``.
        Ticket 08 (import) lists them to attribute an imported CSV to a Bank
        Account.

    get_bank_account(conn, bank_account_id) -> dict | None
        One Bank Account row (incl. ``qbo_account_id``), or None if absent.
        Ticket 10 (journal) resolves the mapped cash-side Account from
        ``qbo_account_id`` against the mirror.

``qbo_account_id`` is a plain stored value (the mirror's ``accounts.qbo_id``), not
a FK: tickets 10/11 treat a Bank Account as "unmapped" when its
``qbo_account_id`` no longer resolves to an *active* mirror Account.
"""

from .service import get_bank_account, list_bank_accounts

__all__ = ["list_bank_accounts", "get_bank_account"]
