"""QBO seam tests. Pure in-memory (no DB) — the fake and the real client's
outcome classification via httpx.MockTransport."""

from decimal import Decimal

import httpx
import pytest

from app.features.qbo import (
    Connection,
    Entry,
    FakeQboClient,
    HttpxQboClient,
    JournalLine,
    Outcome,
)

CONN = Connection(realm_id="4620816365", access_token="at", refresh_token="rt")


def _entry(doc="JE-1"):
    return Entry(
        lines=[
            JournalLine(Decimal("10.00"), "Debit", "2"),
            JournalLine(Decimal("10.00"), "Credit", "1"),
        ],
        doc_number=doc,
        private_note="ledger-sync:entry:abc",
    )


# --- fake: idempotency & core behavior ------------------------------------


async def test_same_requestid_creates_one_entry_same_response():
    fake = FakeQboClient()
    r1 = await fake.post_journal_entry(CONN, _entry(), requestid="rid-1")
    r2 = await fake.post_journal_entry(CONN, _entry(), requestid="rid-1")

    assert r1.ok and r2.ok
    assert r1.data == r2.data                 # same response
    assert r1.data["Id"] == r2.data["Id"]     # not a second entry
    assert len(fake._journals) == 1

    # a different requestid does create a second entry
    r3 = await fake.post_journal_entry(CONN, _entry(doc="JE-2"), requestid="rid-2")
    assert r3.ok and len(fake._journals) == 2


async def test_find_by_doc_number():
    fake = FakeQboClient()
    await fake.post_journal_entry(CONN, _entry(doc="JE-42"), requestid="rid-1")

    found = await fake.find_journal_entry_by_doc_number(CONN, "JE-42")
    assert found.ok and len(found.data) == 1 and found.data[0]["DocNumber"] == "JE-42"

    missing = await fake.find_journal_entry_by_doc_number(CONN, "NOPE")
    assert missing.ok and missing.data == []


async def test_query_accounts_returns_active_and_inactive():
    fake = FakeQboClient()
    r = await fake.query_accounts(CONN)
    assert r.ok
    actives = {a["Active"] for a in r.data}
    assert actives == {True, False}          # Active IN (true, false)


async def test_token_refresh_mints_rotated_tokens():
    fake = FakeQboClient()
    r = await fake.refresh_token(CONN)
    assert r.ok
    assert r.data["access_token"] and r.data["refresh_token"]
    r2 = await fake.refresh_token(CONN)
    assert r2.data["refresh_token"] != r.data["refresh_token"]   # rotated


@pytest.mark.parametrize(
    "outcome", [Outcome.CLIENT_ERROR, Outcome.SERVER_ERROR, Outcome.NETWORK, Outcome.THROTTLED]
)
async def test_fake_can_be_driven_to_each_outcome_class(outcome):
    fake = FakeQboClient()
    fake.fail_next(outcome)
    r = await fake.post_journal_entry(CONN, _entry(), requestid="rid-1")
    assert r.outcome is outcome
    assert not r.ok
    assert len(fake._journals) == 0          # forced failure created nothing


async def test_forced_failure_then_retry_creates_exactly_one():
    """Ticket 11's scenario: first attempt 5xx (unknown), retry same requestid."""
    fake = FakeQboClient()
    fake.fail_next(Outcome.SERVER_ERROR)

    first = await fake.post_journal_entry(CONN, _entry(), requestid="rid-1")
    assert first.outcome is Outcome.SERVER_ERROR

    retry = await fake.post_journal_entry(CONN, _entry(), requestid="rid-1")
    assert retry.ok
    assert len(fake._journals) == 1


# --- real client: outcome classification via MockTransport ----------------


def _client(handler) -> HttpxQboClient:
    return HttpxQboClient(http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_real_client_ok_and_request_shape():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"JournalEntry": {"Id": "7"}})

    r = await _client(handler).post_journal_entry(CONN, _entry(), requestid="rid-9")
    assert r.outcome is Outcome.OK and r.data["Id"] == "7"
    assert "requestid=rid-9" in seen["url"]
    assert "/v3/company/4620816365/journalentry" in seen["url"]


async def test_real_client_query_accounts_unwraps_and_uses_active_filter():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"QueryResponse": {"Account": [{"Id": "1"}]}})

    r = await _client(handler).query_accounts(CONN)
    assert r.ok and r.data == [{"Id": "1"}]
    from urllib.parse import parse_qs, urlparse

    sql = parse_qs(urlparse(seen["url"]).query)["query"][0]
    assert sql == "SELECT * FROM Account WHERE Active IN (true, false)"


@pytest.mark.parametrize(
    "status,expected",
    [(429, Outcome.THROTTLED), (400, Outcome.CLIENT_ERROR), (500, Outcome.SERVER_ERROR)],
)
async def test_real_client_classifies_status(status, expected):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="err")

    r = await _client(handler).query_accounts(CONN)
    assert r.outcome is expected
    assert r.status_code == status
    assert r.error  # surfaced, not swallowed


async def test_real_client_classifies_network_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    r = await _client(handler).query_accounts(CONN)
    assert r.outcome is Outcome.NETWORK and r.status_code is None and r.error
