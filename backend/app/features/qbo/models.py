"""Shapes for the QuickBooks Online seam.

These are the stable, importable inputs/outputs later tickets (05 Connection,
06 Chart of Accounts, 11 Push) wire against. No httpx here on purpose: the fake
and any pure caller can import these without pulling the HTTP client in.

The port is a `typing.Protocol` (structural) — both the real httpx client and
the in-memory fake satisfy it without inheriting anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Protocol


class Outcome(str, Enum):
    """The HTTP outcome class the caller uses to classify retries.

    QBO's retry guidance keys off exactly these buckets: 4xx = not created
    (fix & retry), 5xx/network = unknown (query-before-retry), 429 = throttled
    (wait 60s). 429 is broken out of 4xx because it means "retry later", not
    "your request was wrong".
    """

    OK = "2xx"
    CLIENT_ERROR = "4xx"
    SERVER_ERROR = "5xx"
    NETWORK = "network"
    THROTTLED = "429"


# status_code a forced/synthetic outcome reports; NETWORK has no HTTP response.
_STATUS_FOR = {
    Outcome.OK: 200,
    Outcome.CLIENT_ERROR: 400,
    Outcome.SERVER_ERROR: 500,
    Outcome.THROTTLED: 429,
    Outcome.NETWORK: None,
}


@dataclass
class QboResult:
    """One QBO call's result, outcome class always surfaced (never swallowed)."""

    outcome: Outcome
    status_code: int | None = None
    data: Any = None            # parsed body: account list, JE dict, tokens, ...
    error: str | None = None    # message on a non-OK / network outcome

    @property
    def ok(self) -> bool:
        return self.outcome is Outcome.OK

    @classmethod
    def of(cls, outcome: Outcome, data: Any = None, error: str | None = None) -> "QboResult":
        return cls(outcome=outcome, status_code=_STATUS_FOR[outcome], data=data, error=error)


@dataclass
class Connection:
    """One QBO company connection = one realm + its own token set.

    Credentials (client id/secret) are NOT here — they come only from the
    environment. Ticket 05 owns persisting/rotating these token fields.
    """

    realm_id: str
    access_token: str
    refresh_token: str
    # sandbox: https://sandbox-quickbooks.api.intuit.com
    base_url: str = "https://quickbooks.api.intuit.com"


@dataclass
class JournalLine:
    amount: Decimal              # positive; side is PostingType, not the sign
    posting_type: str            # "Debit" | "Credit"
    account_id: str              # QBO Account.Id -> AccountRef.value


@dataclass
class Entry:
    """A balanced double-entry journal to push. Balancing/precision is the JE
    builder's job (ticket 06); this seam only transports what it's given."""

    lines: list[JournalLine] = field(default_factory=list)
    doc_number: str | None = None    # stable correlation key (not dedup)
    private_note: str | None = None  # free-text stamp for after-the-fact lookup


class QboClient(Protocol):
    """The seam every outbound QBO call goes through. Faked in tests."""

    async def refresh_token(self, connection: Connection) -> QboResult: ...

    async def query_accounts(self, connection: Connection) -> QboResult:
        """Full CoA mirror: Active IN (true, false)."""
        ...

    async def post_journal_entry(
        self, connection: Connection, entry: Entry, requestid: str
    ) -> QboResult:
        """`requestid` makes retries idempotent (same id -> same result, no dup)."""
        ...

    async def find_journal_entry_by_doc_number(
        self, connection: Connection, doc_number: str
    ) -> QboResult: ...
