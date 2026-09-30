"""In-memory QBO fake — the controllable double the money-path tests run on.

Fidelity that ticket 11 (idempotency/retry) leans on:
- `requestid` replay: same (realm, requestid) returns the SAME stored JE and
  creates no second entry — exactly QBO's native idempotency guarantee.
- query-by-DocNumber finds a posted entry.
- query_accounts serves a mixed active+inactive CoA.
- refresh_token mints fresh (rotated) tokens.
- `fail_next(...)` drives any call to return 4xx / 5xx / network / 429.

By default a forced failure does NOT store the JE or record the requestid — so a
first-attempt 5xx/network followed by a same-requestid retry yields exactly one
entry. Two levers model the "created but the response was lost" money-path holes:
- `fail_next(outcome, store_first=True)` stores the JE (under its DocNumber +
  PrivateNote, keyed by requestid) AND returns the forced failure;
- `crash_next_post()` stores the JE then RAISES — the process dies after the POST
  reached QBO but before any DB commit, leaving the entry stranded `attempting`.
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
        self._forced: deque[tuple[Outcome, bool]] = deque()  # (outcome, store_first)
        self._crash_next_post = False
        self._token_seq = 0

    # --- test controls ----------------------------------------------------

    def fail_next(self, outcome: Outcome, times: int = 1, store_first: bool = False) -> None:
        """Force the next `times` call(s) to return `outcome` (non-OK).

        With ``store_first=True`` a forced ``post_journal_entry`` still stores the
        JE (created-but-response-lost) before returning the failure; the default
        stores nothing.
        """
        self._forced.extend([(outcome, store_first)] * times)

    def crash_next_post(self) -> None:
        """The next ``post_journal_entry`` stores the JE then raises — models a
        process death after the POST reached QBO but before any DB commit."""
        self._crash_next_post = True

    def _pop_forced(self) -> tuple[Outcome, bool] | None:
        return self._forced.popleft() if self._forced else None

    def _forced_outcome(self) -> Outcome | None:
        forced = self._pop_forced()
        return forced[0] if forced is not None else None

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

    def _store_je(self, connection: Connection, entry: Entry, requestid: str) -> dict:
        """Create (or replay) the JE for (realm, requestid). Same store the OK path
        uses, so a stored-then-failed POST is indistinguishable from a real one:
        query-by-DocNumber finds it and a same-requestid retry replays it (no dup)."""
        key = (connection.realm_id, requestid)
        if key in self._by_requestid:
            return self._by_requestid[key]  # replay: same requestid -> same JE
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
        return je

    async def post_journal_entry(
        self, connection: Connection, entry: Entry, requestid: str
    ) -> QboResult:
        if self._crash_next_post:
            # Created in QBO, then the process dies before recording the outcome.
            self._crash_next_post = False
            self._store_je(connection, entry, requestid)
            raise RuntimeError("simulated process death: POST reached QBO, no commit")

        forced = self._pop_forced()
        if forced is not None:
            outcome, store_first = forced
            if store_first:
                # Created-but-response-lost: the JE exists, the caller saw a failure.
                self._store_je(connection, entry, requestid)
            return QboResult.of(outcome, error=f"forced {outcome.value}")

        return QboResult.of(Outcome.OK, data=self._store_je(connection, entry, requestid))

    async def find_journal_entry_by_doc_number(
        self, connection: Connection, doc_number: str
    ) -> QboResult:
        forced = self._forced_outcome()
        if forced is not None:
            return QboResult.of(forced, error=f"forced {forced.value}")
        matches = [je for je in self._journals if je.get("DocNumber") == doc_number]
        return QboResult.of(Outcome.OK, data=matches)
