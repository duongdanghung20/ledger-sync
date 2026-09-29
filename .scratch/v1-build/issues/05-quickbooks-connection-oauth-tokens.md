# 05: QuickBooks Connection — OAuth + token lifecycle

**What to build:** The Admin's self-serve link from a Book to a QuickBooks company. An Admin starts an in-app OAuth authorization-code flow; QuickBooks redirects back to `{APP_BASE_URL}/api/qbo/callback`, and the callback captures the `realm_id` and stores the tokens. The refresh token is encrypted at rest with `TOKEN_ENC_KEY` (an authenticated symmetric key held outside the database), so a database dump alone cannot decrypt it, and tokens are never logged. Access tokens refresh on demand, and the rotated refresh token is persisted atomically so a lost token can never lock the Book out. A connection that fails to refresh is marked disconnected, pushes are blocked, and the Admin sees a Reconnect prompt rather than a silent failure; reconnect uses the same flow. Connection management is Admin-only and shows current status.

**Blocked by:** 02, 04.

**Status:** ready-for-agent

- [ ] An Admin completes the OAuth authorization-code flow in-app; the callback at `{APP_BASE_URL}/api/qbo/callback` records the `realm_id` and the connection.
- [ ] The refresh token is stored encrypted at rest with a key from `TOKEN_ENC_KEY`; a raw database read cannot recover it; tokens never appear in logs.
- [ ] An expired access token refreshes on demand; the rotated refresh token is persisted atomically (a failure mid-refresh never leaves the Book with no usable token).
- [ ] A refresh that fails marks the Connection disconnected, blocks pushes, and surfaces a Reconnect prompt.
- [ ] Reconnect runs the same flow and restores a connected status.
- [ ] Connection status is one of `{pending, connected, disconnected}`; connection management routes and UI are Admin-only.
