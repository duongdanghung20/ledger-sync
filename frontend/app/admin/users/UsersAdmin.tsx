"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import styles from "./users.module.css";

type Role = "Admin" | "Bookkeeper";

type User = {
  id: string;
  email: string;
  role: Role;
  must_set_password: boolean;
  disabled: boolean;
};

function statusOf(u: User): string {
  if (u.disabled) return "No access";
  if (u.must_set_password) return "Invitation pending";
  return "Active";
}

export function UsersAdmin() {
  const [users, setUsers] = useState<User[]>([]);
  const [meId, setMeId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<string | null>(null); // user id (or "invite") mid-action

  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("Bookkeeper");
  const [handoff, setHandoff] = useState<{ email: string; link: string } | null>(null);

  const load = useCallback(async () => {
    setError("");
    try {
      const [meRes, listRes] = await Promise.all([fetch("/api/me"), fetch("/api/users")]);
      if (listRes.status === 401) {
        setError("Sign in as an admin to manage people.");
        setUsers([]);
        return;
      }
      if (listRes.status === 403) {
        setError("Only admins can manage people.");
        setUsers([]);
        return;
      }
      if (!listRes.ok) throw new Error(String(listRes.status));
      setUsers(await listRes.json());
      setMeId(meRes.ok ? (await meRes.json()).id : null);
    } catch {
      setError("Couldn't reach the server. Try again.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function invite(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    setBusy("invite");
    try {
      const res = await fetch("/api/users/invite", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim(), role }),
      });
      if (res.status === 409) {
        setError("Someone already has that email.");
        return;
      }
      if (!res.ok) throw new Error(String(res.status));
      const body = await res.json();
      setHandoff({ email: body.user.email, link: body.setup_link });
      setEmail("");
      await load();
    } catch {
      setError("Couldn't send the invite. Try again.");
    } finally {
      setBusy(null);
    }
  }

  async function patchUser(u: User, change: { role?: Role; disabled?: boolean }) {
    setError("");
    setBusy(u.id);
    try {
      const res = await fetch(`/api/users/${u.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(change),
      });
      if (!res.ok) throw new Error(String(res.status));
      await load();
    } catch {
      setError("Couldn't apply that change. Try again.");
    } finally {
      setBusy(null);
    }
  }

  async function reset(u: User) {
    setError("");
    setBusy(u.id);
    try {
      const res = await fetch(`/api/users/${u.id}/reset`, { method: "POST" });
      if (!res.ok) throw new Error(String(res.status));
      const body = await res.json();
      setHandoff({ email: u.email, link: body.setup_link });
    } catch {
      setError("Couldn't issue a reset link. Try again.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <main className={styles.wrap}>
      <Link href="/" className={styles.back}>
        Ledger-Sync
      </Link>
      <h1 className={styles.title}>People</h1>
      <p className={styles.intro}>
        Who can open the books. Invite someone with a role, reassign it, or revoke access.
        Disabling a person or changing their role ends their sessions at once.
      </p>

      <form className={styles.invite} onSubmit={invite}>
        <div className={`${styles.field} ${styles.grow}`}>
          <label htmlFor="invite-email">Email</label>
          <input
            id="invite-email"
            className={styles.input}
            type="email"
            autoComplete="off"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div className={styles.field}>
          <label htmlFor="invite-role">Role</label>
          <select
            id="invite-role"
            className={styles.select}
            value={role}
            onChange={(e) => setRole(e.target.value as Role)}
          >
            <option value="Bookkeeper">Bookkeeper</option>
            <option value="Admin">Admin</option>
          </select>
        </div>
        <button className={styles.post} type="submit" disabled={busy === "invite"}>
          {busy === "invite" ? "Sending…" : "Send invite"}
        </button>
      </form>
      <div className={styles.close} aria-hidden="true" />

      {handoff && (
        <div className={styles.handoff}>
          <p>
            Set-password link for <strong>{handoff.email}</strong>. Hand it over directly — it
            works once and expires.
          </p>
          <input
            className={styles.linkline}
            readOnly
            value={handoff.link}
            aria-label="Set-password link"
            onFocus={(e) => e.currentTarget.select()}
          />
        </div>
      )}

      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}

      <section className={styles.roster} aria-label="People">
        {loading ? (
          <p className={styles.muted} style={{ paddingTop: "0.85rem" }}>
            Loading…
          </p>
        ) : users.length === 0 ? (
          !error && (
            <p className={styles.muted} style={{ paddingTop: "0.85rem" }}>
              No one yet. Invite the first person above.
            </p>
          )
        ) : (
          users.map((u) => {
            const isMe = u.id === meId;
            const pending = busy === u.id;
            return (
              <div key={u.id} className={`${styles.row} ${u.disabled ? styles.voided : ""}`}>
                <span className={styles.who}>
                  {u.email}
                  {isMe && <span className={styles.you}> · you</span>}
                </span>
                <span className={styles.status}>{statusOf(u)}</span>
                <div className={styles.rowRole}>
                  {isMe ? (
                    <span className={styles.status}>{u.role}</span>
                  ) : (
                    <select
                      className={styles.select}
                      aria-label={`Role for ${u.email}`}
                      value={u.role}
                      disabled={pending}
                      onChange={(e) => patchUser(u, { role: e.target.value as Role })}
                    >
                      <option value="Bookkeeper">Bookkeeper</option>
                      <option value="Admin">Admin</option>
                    </select>
                  )}
                </div>
                <div className={styles.rowActions}>
                  <button className={styles.textbtn} disabled={pending} onClick={() => reset(u)}>
                    Reset password
                  </button>
                  {!isMe &&
                    (u.disabled ? (
                      <button
                        className={styles.textbtn}
                        disabled={pending}
                        onClick={() => patchUser(u, { disabled: false })}
                      >
                        Restore access
                      </button>
                    ) : (
                      <button
                        className={`${styles.textbtn} ${styles.danger}`}
                        disabled={pending}
                        onClick={() => patchUser(u, { disabled: true })}
                      >
                        Disable
                      </button>
                    ))}
                </div>
              </div>
            );
          })
        )}
      </section>
    </main>
  );
}
