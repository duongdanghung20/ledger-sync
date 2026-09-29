"""OAuth2 authorization-code bootstrap for a QuickBooks connection.

The qbo port (ticket 04) exposes only the *refresh_token* grant and
``app/features/qbo/**`` is off-limits, so the one-time *authorization_code*
exchange lives here — it hits the same Intuit token endpoint with the same
Basic auth the port uses for refresh. Every later token refresh still goes
through the port. Nothing here logs a code or a token.

Flow (per .scratch/v1-spec/research/qbo-integration.md, cited to Intuit):
- authorize:  GET  https://appcenter.intuit.com/connect/oauth2?...
- token:      POST https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer
"""

from __future__ import annotations

from urllib.parse import urlencode

import httpx

from app.config import env
from app.features.qbo import Outcome, QboResult

_AUTHORIZE_URL = "https://appcenter.intuit.com/connect/oauth2"
_TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
_SCOPE = "com.intuit.quickbooks.accounting"  # the only scope v1 needs
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def callback_url() -> str:
    """The redirect URI — must match what's registered in the Intuit portal and
    is sent identically on both the authorize request and the token exchange."""
    return f"{env('APP_BASE_URL', 'http://localhost:8080').rstrip('/')}/api/qbo/callback"


def authorization_url(state: str) -> str:
    query = urlencode(
        {
            "client_id": env("QBO_CLIENT_ID"),
            "response_type": "code",
            "scope": _SCOPE,
            "redirect_uri": callback_url(),
            "state": state,
        }
    )
    return f"{_AUTHORIZE_URL}?{query}"


def _classify(status_code: int) -> Outcome:
    if status_code == 429:
        return Outcome.THROTTLED
    if 400 <= status_code < 500:
        return Outcome.CLIENT_ERROR
    return Outcome.SERVER_ERROR


async def exchange_code(code: str) -> QboResult:
    """Exchange an authorization code for the initial token set. Returns a
    ``QboResult`` (OK -> ``data`` is the token dict). On a non-OK status the
    response body is NOT surfaced — it can carry token material."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
            resp = await http.post(
                _TOKEN_URL,
                auth=(env("QBO_CLIENT_ID"), env("QBO_CLIENT_SECRET")),
                headers={"Accept": "application/json"},
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": callback_url(),
                },
            )
    except httpx.RequestError as exc:  # timeout, connect, DNS, ...
        return QboResult(Outcome.NETWORK, None, error=str(exc))

    if 200 <= resp.status_code < 300:
        return QboResult(Outcome.OK, resp.status_code, data=resp.json())
    return QboResult(_classify(resp.status_code), resp.status_code, error="token exchange failed")
