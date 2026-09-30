"""The pure money-path core of journal materialization — no DB, no I/O.

Everything here is a pure function over Decimals so the sign rule can be tested
in isolation (and re-run to reconstruct a stored entry's two lines — there is no
Journal-Entry lines table). The rule is account-type-agnostic and posts
``abs(amount)`` on both lines with opposite ``PostingType``, so debits always
equal credits by construction and QuickBooks error 2300 is structurally
impossible.

Sign rule (signed canonical amount ``A``, negative = outflow of the Bank Account):

    A < 0 (outflow):  Debit category |A|,  Credit cash     |A|
    A > 0 (inflow):   Debit cash     |A|,  Credit category |A|
    A = 0:            rejected

Public seam (import from ``app.features.journal``)::

    build_lines(signed_amount, category_qbo_id, cash_qbo_id) -> (debit, credit)
    preview(txn_row) -> (debit, credit)          # needs cash_account_qbo_id on the row
    to_port_entry(je_row) -> qbo.Entry           # ticket 11 pushes this
"""

from __future__ import annotations

from decimal import Decimal

from app.features.qbo import Entry, JournalLine

_CENTS = Decimal("0.01")


# --- errors (router maps .status_code -> HTTP) --------------------------------


class JournalError(Exception):
    """Base for a materialization refusal; ``status_code`` is the HTTP mapping."""

    status_code = 422


class ZeroAmount(JournalError):
    status_code = 422


class Uncategorized(JournalError):
    status_code = 409


class UnmappedBankAccount(JournalError):
    status_code = 409


class AlreadyApproved(JournalError):
    status_code = 409


class NotPending(JournalError):
    status_code = 409


class NotFound(JournalError):
    status_code = 404


# --- the sign rule ------------------------------------------------------------


def build_lines(
    signed_amount: Decimal, category_qbo_id: str, cash_qbo_id: str
) -> tuple[JournalLine, JournalLine]:
    """The one sign rule. Returns ``(debit_line, credit_line)`` — both posting the
    same ``abs(signed_amount)`` with opposite ``PostingType`` (so Dr == Cr). Raises
    ``ZeroAmount`` for A == 0. Amount is Decimal, 2dp — never float."""
    if not isinstance(signed_amount, Decimal):  # money-path trust boundary
        raise TypeError(f"amount must be Decimal, not {type(signed_amount).__name__}")
    amt = signed_amount.quantize(_CENTS)
    if amt == 0:
        raise ZeroAmount("A zero-amount transaction has no journal entry.")
    abs_amt = abs(amt)
    if amt < 0:  # outflow: Debit category, Credit cash
        debit_id, credit_id = category_qbo_id, cash_qbo_id
    else:  # inflow: Debit cash, Credit category
        debit_id, credit_id = cash_qbo_id, category_qbo_id
    return (
        JournalLine(amount=abs_amt, posting_type="Debit", account_id=debit_id),
        JournalLine(amount=abs_amt, posting_type="Credit", account_id=credit_id),
    )


def debit_side(debit_line: JournalLine, category_qbo_id: str) -> str:
    """Which side carries the debit, as stored in the snapshot: ``category`` for an
    outflow, ``cash`` for an inflow."""
    return "category" if debit_line.account_id == category_qbo_id else "cash"


def preview(txn_row: dict) -> tuple[JournalLine, JournalLine]:
    """The two lines a categorized transaction *would* post, without materializing.
    The row must carry signed ``amount``, ``assigned_account_qbo_id`` (category) and
    ``cash_account_qbo_id`` (the Bank Account's mapped cash Account). Raises
    ``Uncategorized`` when no account is assigned."""
    category = txn_row.get("assigned_account_qbo_id")
    if not category:
        raise Uncategorized("Assign an account before this transaction can be journaled.")
    return build_lines(txn_row["amount"], category, txn_row["cash_account_qbo_id"])


def to_port_entry(je_row: dict) -> Entry:
    """Map a stored Journal-Entry snapshot to the qbo port's ``Entry`` (ticket 11
    pushes this). Reconstructs the two lines by re-running the one sign rule over a
    signed amount rebuilt from ``debit_side`` — the rule stays the single source of
    truth. The full entry id is stamped into ``PrivateNote`` for correlation."""
    amount = je_row["amount"]  # abs Decimal
    signed = -amount if je_row["debit_side"] == "category" else amount
    debit, credit = build_lines(
        signed, je_row["category_account_qbo_id"], je_row["cash_account_qbo_id"]
    )
    stamp = f"[ledger-sync:{je_row['id']}]"
    memo = (je_row.get("memo") or "").strip()
    private_note = f"{memo} {stamp}".strip()
    return Entry(
        lines=[debit, credit],
        doc_number=str(je_row["doc_number"]),
        private_note=private_note,
    )


if __name__ == "__main__":  # money-path self-check (no DB): balance + sign + zero
    out = build_lines(Decimal("-6.75"), "cat", "cash")
    assert out[0].posting_type == "Debit" and out[0].account_id == "cat"
    assert out[1].posting_type == "Credit" and out[1].account_id == "cash"
    assert out[0].amount == out[1].amount == Decimal("6.75")

    inflow = build_lines(Decimal("9000.00"), "cat", "cash")
    assert inflow[0].account_id == "cash" and inflow[1].account_id == "cat"
    assert inflow[0].amount == inflow[1].amount == Decimal("9000.00")

    try:
        build_lines(Decimal("0.00"), "cat", "cash")
        raise SystemExit("zero amount was not rejected")
    except ZeroAmount:
        pass

    je = {"id": "abc", "amount": Decimal("6.75"), "debit_side": "category",
          "category_account_qbo_id": "cat", "cash_account_qbo_id": "cash",
          "memo": "STARBUCKS", "doc_number": 1}
    entry = to_port_entry(je)
    assert sum(l.amount for l in entry.lines if l.posting_type == "Debit") == \
           sum(l.amount for l in entry.lines if l.posting_type == "Credit")
    assert entry.doc_number == "1" and "ledger-sync:abc" in entry.private_note
    print("journal engine self-check ok")
