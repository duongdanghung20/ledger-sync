"""In-memory QBO fake — the controllable double the money-path tests run on.

Fidelity that ticket 11 (idempotency/retry) leans on:
- `requestid` replay: same (realm, requestid) returns the SAME stored JE and
  creates no second entry — exactly QBO's native idempotency guarantee.
- query-by-DocNumber finds a posted entry.
- query_accounts serves a mixed active+inactive CoA.
- refresh_token mints fresh (rotated) tokens.
- `fail_next(...)` drives any call to return 4xx / 5xx / network / 429.

A forced failure does NOT store the JE or record the requestid — so a
first-attempt 5xx/network followed by a same-requestid retry yields exactly one
entry, which is the retry scenario under test.
"""

from __future__ import annotations

from collections import deque

from .models import Connection, Entry, Outcome, QboResult


def _default_accounts() -> list[dict]:
    """A minimal CoA mirror with both an active and an inactive account."""
    return [
        {"Id": "1", "Name": "Checking", "AccountType": "Bank",
         "Classification": "Asset", "Active": True},
        {"Id": "2", "Name": "Office Supplies", "AccountType": "Expense",
         "Classification": "Expense", "Active": True},
        {"Id": "99", "Name": "Old Petty Cash", "AccountType": "Bank",
         "Classification": "Asset", "Active": False},
    ]


class FakeQboClient:
    """Structurally satisfies models.QboClient."""

    def __init__(self, accounts: list[dict] | None = None) -> None:
        self._accounts = accounts if accounts is not None else _default_accounts()
        self._journals: list[dict] = []                 # every created JE
        self._by_requestid: dict[tuple[str, str], dict] = {}  # (realm, rid) -> JE
        self._forced: deque[Outcome] = deque()
        self._token_seq = 0

    # --- test controls ----------------------------------------------------

    def fail_next(self, outcome: Outcome, times: int = 1) -> None:
        """Force the next `times` call(s) to return `outcome` (non-OK)."""
        self._forced.extend([outcome] * times)

    def _forced_outcome(self) -> Outcome | None:
        return self._forced.popleft() if self._forced else None

    # --- port -------------------------------------------------------------

    async def refresh_token(self, connection: Connection) -> QboResult:
        forced = self._forced_outcome()
        if forced is not None:
            return QboResult.of(forced, error=f"forced {forced.value}")
        self._token_seq += 1
        n = self._token_seq
        return QboResult.of(
            Outcome.OK,
            data={
                "access_token": f"fake-access-{n}",
                "refresh_token": f"fake-refresh-{n}",  # rotated on every refresh
                "expires_in": 3600,
                "x_refresh_token_expires_in": 8726400,
                "token_type": "bearer",
            },
        )

    async def query_accounts(self, connection: Connection) -> QboResult:
        forced = self._forced_outcome()
        if forced is not None:
            return QboResult.of(forced, error=f"forced {forced.value}")
        return QboResult.of(Outcome.OK, data=list(self._accounts))

    async def post_journal_entry(
        self, connection: Connection, entry: Entry, requestid: str
    ) -> QboResult:
        forced = self._forced_outcome()
        if forced is not None:
            # No store, no replay record: the caller retries with the same id.
            return QboResult.of(forced, error=f"forced {forced.value}")

        key = (connection.realm_id, requestid)
        if key in self._by_requestid:
            # Replay: same requestid -> same JE, no second entry created.
            return QboResult.of(Outcome.OK, data=self._by_requestid[key])

        je = {
            "Id": str(len(self._journals) + 1),
            "DocNumber": entry.doc_number,
            "PrivateNote": entry.private_note,
            "Line": [
                {
                    "Amount": float(line.amount),
                    "DetailType": "JournalEntryLineDetail",
                    "JournalEntryLineDetail": {
                        "PostingType": line.posting_type,
                        "AccountRef": {"value": line.account_id},
                    },
                }
                for line in entry.lines
            ],
        }
        self._journals.append(je)
        self._by_requestid[key] = je
        return QboResult.of(Outcome.OK, data=je)

    async def find_journal_entry_by_doc_number(
        self, connection: Connection, doc_number: str
    ) -> QboResult:
        forced = self._forced_outcome()
        if forced is not None:
            return QboResult.of(forced, error=f"forced {forced.value}")
        matches = [je for je in self._journals if je.get("DocNumber") == doc_number]
        return QboResult.of(Outcome.OK, data=matches)
