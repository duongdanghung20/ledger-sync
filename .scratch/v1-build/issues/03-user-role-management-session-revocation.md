# 03: User & role management, session revocation

**What to build:** The Admin's control over who has access. An Admin invites a new User with a chosen Role (Admin or Bookkeeper) and gets a set-password link to hand over out-of-band — no email infrastructure. An Admin changes a User's Role, disables a User (revoking access without deleting the record), and triggers a password reset that reissues the same set-password link. Disabling a User or changing their Role revokes that User's live Sessions immediately, so an access change takes effect at once rather than at Session expiry. Every Admin-only route enforces the Role on the server, so a tampered or Bookkeeper frontend cannot reach it. A minimal Admin screen lists Users and exposes invite / change-role / disable / reset.

**Blocked by:** 02.

**Status:** ready-for-agent

- [ ] An Admin invites a User with a chosen Role; the response yields a set-password link (reusing the link primitive), and the invited person becomes a User on redemption.
- [ ] An Admin changes a User's Role and disables a User; a disabled User can neither log in nor use an existing Session.
- [ ] An Admin-triggered reset reissues a set-password link for an existing User.
- [ ] Disabling a User or changing their Role revokes that User's active Sessions immediately (a previously valid Session stops working without waiting for expiry).
- [ ] Every Admin-only route rejects a Bookkeeper with a server-side authorization failure, proven per route.
- [ ] A minimal Admin Users screen performs invite, change-role, disable, and reset.
