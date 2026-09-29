"""Real httpx-backed QuickBooks Online client.

The ONLY module that touches httpx for QBO. Every method returns a QboResult
with the outcome class surfaced (2xx / 4xx / 5xx / network / 429) — never
raises on a bad status, so callers own the retry decision.

Endpoints (per .scratch/v1-spec/research/qbo-integration.md, cited to Intuit):
- token refresh: POST https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer
- account query / JE query: GET  {base}/v3/company/{realm}/query?query=<SQL>
- journal entry POST:       POST {base}/v3/company/{realm}/journalentry?requestid=<id>
"""

from __future__ import annotations

from dataclasses import replace

import httpx

from app.config import env

from .models import Connection, Entry, Outcome, QboResult

_TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
# ponytail: minorversion pinned to one value; bump here when the JE builder
# (ticket 06) needs a newer field set.
_MINOR_VERSION = "75"
# QBO times out any request past 120s; keep well under and fail fast on connect.
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def _classify(status_code: int) -> Outcome:
    if status_code == 429:
        return Outcome.THROTTLED
    if 200 <= status_code < 300:
        return Outcome.OK
    if 400 <= status_code < 500:
        return Outcome.CLIENT_ERROR
    return Outcome.SERVER_ERROR  # 5xx (and any other >= 500)


def _entry_payload(entry: Entry) -> dict:
    lines = [
        {
            # ponytail: amount serialized via float() at the wire boundary; if
            # sub-cent fidelity ever matters, send a Decimal-aware raw number.
            "Amount": float(line.amount),
            "DetailType": "JournalEntryLineDetail",
            "JournalEntryLineDetail": {
                "PostingType": line.posting_type,
                "AccountRef": {"value": line.account_id},
            },
        }
        for line in entry.lines
    ]
    payload: dict = {"Line": lines}
    if entry.doc_number is not None:
        payload["DocNumber"] = entry.doc_number
    if entry.private_note is not None:
        payload["PrivateNote"] = entry.private_note
    return payload


class HttpxQboClient:
    """Structurally satisfies models.QboClient.

    Pass an `httpx.AsyncClient` to reuse a pooled client (and to inject a
    MockTransport in tests); otherwise one is created per call.
    """

    def __init__(
        self,
        http: httpx.AsyncClient | None = None,
        minor_version: str = _MINOR_VERSION,
    ) -> None:
        self._http = http
        self._minor_version = minor_version

    # --- transport --------------------------------------------------------

    async def _send(self, method: str, url: str, **kwargs) -> QboResult:
        try:
            if self._http is not None:
                resp = await self._http.request(method, url, **kwargs)
            else:
                async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
                    resp = await http.request(method, url, **kwargs)
        except httpx.RequestError as exc:  # timeouts, connect errors, DNS, ...
            return QboResult(Outcome.NETWORK, None, error=str(exc))

        outcome = _classify(resp.status_code)
        try:
            body = resp.json()
        except ValueError:
            body = None
        return QboResult(
            outcome=outcome,
            status_code=resp.status_code,
            data=body,
            error=None if outcome is Outcome.OK else (resp.text or None),
        )

    def _company_url(self, connection: Connection, path: str) -> str:
        return f"{connection.base_url}/v3/company/{connection.realm_id}/{path}"

    def _headers(self, connection: Connection) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {connection.access_token}",
            "Accept": "application/json",
        }

    # --- port -------------------------------------------------------------

    async def refresh_token(self, connection: Connection) -> QboResult:
        # Basic auth = client_id:client_secret; read from env at call time.
        return await self._send(
            "POST",
            _TOKEN_URL,
            auth=(env("QBO_CLIENT_ID"), env("QBO_CLIENT_SECRET")),
            headers={"Accept": "application/json"},
            data={
                "grant_type": "refresh_token",
                "refresh_token": connection.refresh_token,
            },
        )

    async def query_accounts(self, connection: Connection) -> QboResult:
        result = await self._query(
            connection, "SELECT * FROM Account WHERE Active IN (true, false)"
        )
        if result.ok and isinstance(result.data, dict):
            accounts = result.data.get("QueryResponse", {}).get("Account", [])
            return replace(result, data=accounts)
        return result

    async def find_journal_entry_by_doc_number(
        self, connection: Connection, doc_number: str
    ) -> QboResult:
        safe = doc_number.replace("'", "\\'")
        result = await self._query(
            connection, f"SELECT * FROM JournalEntry WHERE DocNumber = '{safe}'"
        )
        if result.ok and isinstance(result.data, dict):
            entries = result.data.get("QueryResponse", {}).get("JournalEntry", [])
            return replace(result, data=entries)
        return result

    async def post_journal_entry(
        self, connection: Connection, entry: Entry, requestid: str
    ) -> QboResult:
        result = await self._send(
            "POST",
            self._company_url(connection, "journalentry"),
            params={"requestid": requestid, "minorversion": self._minor_version},
            headers={**self._headers(connection), "Content-Type": "application/json"},
            json=_entry_payload(entry),
        )
        if result.ok and isinstance(result.data, dict):
            return replace(result, data=result.data.get("JournalEntry", result.data))
        return result

    async def _query(self, connection: Connection, sql: str) -> QboResult:
        return await self._send(
            "GET",
            self._company_url(connection, "query"),
            params={"query": sql, "minorversion": self._minor_version},
            headers=self._headers(connection),
        )
