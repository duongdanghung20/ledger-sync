# Authentication and RBAC

Type: grilling
Status: resolved

## Question

Design authentication and role-based access for the Organization. Roles are Admin and Bookkeeper, admin-invite only, applied Organization-wide across all the Organization's Books (ADR-0001, ADR-0003); a User belongs to the Organization and in v1 accesses all its Books.

- Auth mechanism between the Next.js frontend and FastAPI backend (session cookie vs JWT).
- Admin-invite user flow (no self-signup) and how the first Admin — with the Organization and its initial Book — is bootstrapped on a fresh instance.
- Role enforcement points: what Admin can do that Bookkeeper cannot, enforced server-side per endpoint.
- Credential handling (password hashing) vs an external identity provider — default to local; record if an IdP is ruled out.

## Answer

Local-password auth with server-side sessions, admin-invite only, two Organization-wide roles. Decisions (grilling Q1–Q6):

**Identity source (Q1).** Local passwords hashed with **Argon2id** at library defaults; no external identity provider in v1. Rationale: single-Organization, admin-invite, few Users; an IdP would force every self-hoster to register an OAuth app just to log a human in. The QBO OAuth (ticket 02) is a data connection, unrelated to human login. "No external IdP in v1" recorded as **ADR-0004** (hard to reverse, future-reader-surprising).

**Session mechanism (Q2).** **httpOnly + Secure + SameSite session cookie** carrying an opaque session id; **sessions stored in Postgres** (already in the stack). Chosen over JWT: revocable (an Admin disabling a User or changing a role invalidates live sessions — impossible with a stateless JWT without extra machinery), no token exposed to JS. Topology assumption: frontend (Next.js) and backend (FastAPI) sit behind one reverse proxy at `APP_BASE_URL`, same origin. If ever cross-origin, `SameSite`/CORS reopens.

**Role permission matrix (Q3).** Two roles, Organization-wide (ADR-0001, ADR-0003), **Admin ⊇ Bookkeeper**. Bookkeeper owns the operational money path; Admin adds config + people.

| Capability | Bookkeeper | Admin |
|---|---|---|
| Import CSV, manage Column-Mapping Profiles | ✅ | ✅ |
| Categorize: Categorization Rules + manual override | ✅ | ✅ |
| Refresh Chart of Accounts | ✅ | ✅ |
| Review / approve / push Journal Entries | ✅ | ✅ |
| Manage QuickBooks Connection (connect/reconnect OAuth) | ❌ | ✅ |
| Manage Users (invite, change role, disable) | ❌ | ✅ |
| Create Bank Account + set its 1:1 `qbo_account_id` mapping | ❌ | ✅ |

Enforced **server-side**: a FastAPI dependency per route asserts the required role; the frontend is never trusted for authorization. The three Admin-exclusive areas: QuickBooks Connection, User management, Bank Account creation/mapping (mapping is a money-path config decision — Admin).

**Invitation + reset delivery (Q4).** **No email in v1.** One primitive: a one-time **set-password link** (signed token, expiry, single-use). Admin invites a User → system shows the link → Admin delivers it out-of-band → User opens it, sets password. Password reset is the same primitive, Admin-triggered (self-service "forgot password" is impossible without email; Admin-mediated covers it). New domain term **Invitation** (target identifier, Role, token, expiry, consumed flag). SMTP is a later, non-blocking add.

**First-run bootstrap (Q5).** `BOOTSTRAP_ADMIN_EMAIL` env var. On first boot with zero Users, the app creates the Organization + its auto-created initial Book (ADR-0003) + the Admin for that email, marks the Admin "must set password", and **prints a set-password link to the container logs** (reuses Q4's primitive). No password in env (avoids creds lingering in compose files); no open unauthenticated setup screen, so no boot-time race — the operator fixes the admin identity. Org name via `ORG_NAME` env (or defaulted), editable later. QBO connect is a post-bootstrap Admin action (ticket 02), not part of first-run.

**Password + session policy (Q6).** Password: Argon2id at library defaults (cost params not hand-tuned), min length **12**, no forced composition, no forced rotation (NIST 800-63B); no breach-list (HIBP) check in v1. Session: opaque id in Postgres, **absolute expiry 14 days**, revoked immediately on User disable or role change.

**New env vars:** `BOOTSTRAP_ADMIN_EMAIL`, `ORG_NAME` (optional). Joins ticket 02's `QBO_CLIENT_ID`/`QBO_CLIENT_SECRET`/`TOKEN_ENC_KEY`/`APP_BASE_URL`. A session-signing secret is also needed (e.g. `SESSION_SECRET`) if sessions use signed cookie ids.

**New schema (for the unified data model, /to-spec):** `User` (email, Argon2id hash, Role, disabled flag, must-set-password flag), `Session` (opaque id, User, created/expires), `Invitation` (target identifier, Role, token, expiry, consumed). Role stays an enum {Admin, Bookkeeper}, Organization-wide.

**Consequences:** graduates the "First-run setup / bootstrap" fog (now decided here) and supplies the last tables the "Unified data model" fog was waiting on. ADR-0004 written. No new tickets surfaced — auth was the final decision gating the unified model, which is /to-spec assembly, not a decision.
