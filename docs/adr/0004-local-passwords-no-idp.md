# Local passwords, no external identity provider in v1

Ledger-Sync authenticates Users with local passwords (Argon2id) and server-side sessions, and does **not** integrate an external identity provider (OIDC/SAML — Google, Okta, etc.) in v1.

A Ledger-Sync instance serves one Organization (ADR-0001), is admin-invite only, and has few Users (one Admin plus a handful of Bookkeepers). An external IdP would force every self-hoster to register an OAuth application and wire client credentials into the instance just to log a human in — significant setup friction for a single-Organization box, and infrastructure a small self-hoster may not have. Local passwords keep the human-login path self-contained.

This is distinct from the QuickBooks Connection's OAuth (ADR-0002, ticket 02), which authorizes a *data* connection to a QuickBooks company and is unrelated to human login. Choosing local passwords for login does not touch it.

We record this deliberately because adding an IdP later is a meaningful change (new login flow, User-identity mapping, session issuance) and a future reader will reasonably ask why v1 shipped local passwords. IdP support is out of scope for v1, revisitable as a later effort if multi-Organization or SSO requirements appear.

Consequences:
- Passwords hashed with Argon2id at library defaults; policy min length 12, no forced composition or rotation, no breach-list check (NIST 800-63B).
- Sessions are opaque server-side records in Postgres, delivered via httpOnly cookie, revocable on User disable or Role change — chosen over stateless JWT for revocability at single-instance scale.
- No email in v1: Invitations and password resets use a single-use, expiring set-password link the Admin delivers out-of-band. Adding SMTP later is non-breaking.
- First Admin is bootstrapped from `BOOTSTRAP_ADMIN_EMAIL`; the set-password link is printed to container logs on first boot.
