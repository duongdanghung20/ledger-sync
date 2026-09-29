# QBO connection and credential storage

Type: grilling
Status: resolved
Blocked by: 01

## Question

Design how a Book connects to QuickBooks Online and where credentials live. One QuickBooks Connection per Book (one `realmID`); one registered app authorizes per company, yielding one token set per `realmId`, and one instance holds several concurrently (ticket 01).

- In-app OAuth connect flow (Admin-only) vs a one-time setup wizard.
- Storage of the app's client secret and each Book's OAuth tokens: encrypted at rest, Book-scoped. Decide the boundary — app credentials in env, per-Book tokens in Postgres (encrypted)?
- Token refresh handling (access ~1h, refresh ~101 days, rotates) and the reconnect-on-expiry experience.
- QBO app registration steps on the Intuit developer portal — candidate for `/wizard` (a human-only setup task).

Security: refresh tokens encrypted at rest; tokens never logged. Uses facts from ticket 01 (`research/qbo-integration.md`).

## Answer

One QuickBooks Connection per Book, `realmId`-scoped.

- **Connect flow** (Q1): in-app OAuth2 authorization-code, Admin-only. Admin clicks "Connect QuickBooks" on the Book → redirect to Intuit → callback stores tokens for that Book. Reconnect is the same self-serve flow.
- **App credentials** (Q2): operator registers one Intuit app per instance; `client_id`/`client_secret` supplied via env at deploy, never in the DB or UI. One app authorizes many `realmId`s (ticket 01).
- **Token storage** (Q3): a per-Book row in Postgres holds the `realmId`, access token (+ expiry), and refresh token; the refresh token is encrypted at rest.
- **Encryption key** (Q4): a single app key from env (`TOKEN_ENC_KEY`, 32-byte), authenticated symmetric encryption via a vetted library (`cryptography` Fernet / AES-GCM). The key stays out of the DB, so a DB dump alone cannot decrypt; key rotation = re-encrypt (documented, rare).
- **Refresh + reconnect** (Q5): refresh on demand when the access token is expired or near-expiry; persist the rotated refresh token atomically on every refresh (hard requirement — losing it locks the Book out). On refresh failure (expired/revoked) → set the Connection status to `disconnected`, block pushes, and surface "Reconnect" to the Admin.
- **Redirect URI** (Q6): operator sets `APP_BASE_URL` in env; redirect URI = `{APP_BASE_URL}/api/qbo/callback`, registered on the Intuit app during the one-time registration wizard.

**Security**: refresh tokens encrypted at rest with an env-held key; tokens never logged; app credentials env-only.

**Surfaced**: the QuickBooks Connection entity fields (`realmId`, encrypted refresh token, access token + expiry, status `pending`/`connected`/`disconnected`, `connected_at`) feed the unified-schema fog. The one-time Intuit app registration + env setup is a `/wizard` build-time task folded into first-run bootstrap fog — no separate decision ticket.

**Env vars introduced**: `QBO_CLIENT_ID`, `QBO_CLIENT_SECRET`, `TOKEN_ENC_KEY`, `APP_BASE_URL`.
