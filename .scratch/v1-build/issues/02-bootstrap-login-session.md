# 02: Bootstrap, login, and server-side session

**What to build:** The first way in. On first boot with no Users, the app reads `BOOTSTRAP_ADMIN_EMAIL` (and `ORG_NAME`, or a sensible default), creates the Organization, its single Book, and the first Admin, and prints a single-use, expiring set-password link to the container logs — no password ever lives in a compose file or environment variable. Anyone with that link sets a password (hashed with Argon2id) once. A User then logs in with email + password and receives an httpOnly session cookie backed by a server-side Session record that expires at a fixed 14-day absolute lifetime; logout ends the session. `GET /api/me` returns the authenticated User. A server-side session-validation dependency and a reusable role-asserting guard are in place for every later route to build on.

**Blocked by:** 01.

**Status:** ready-for-agent

- [ ] On first boot with an empty Users table, the Organization, its one Book, and the first Admin (from `BOOTSTRAP_ADMIN_EMAIL`) are created; the Organization name comes from `ORG_NAME` or a documented default and is editable later.
- [ ] A single-use, expiring set-password link is printed to the container logs on bootstrap; no password is read from env or a compose file.
- [ ] Redeeming a set-password link sets an Argon2id password hash; the link cannot be reused and is rejected after expiry.
- [ ] Login with valid credentials issues an httpOnly, server-side-backed Session cookie carrying no readable credential.
- [ ] The Session expires at a fixed 14-day absolute lifetime; an expired Session is rejected.
- [ ] Logout invalidates the server-side Session immediately.
- [ ] `GET /api/me` returns the current User for a valid Session and 401 without one.
- [ ] Schema exists for Organization, User (email, Argon2id hash, role, disabled flag, must-set-password flag), Session (opaque id, user, created_at, expires_at), Invitation, and Book.
