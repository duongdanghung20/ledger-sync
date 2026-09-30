"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import styles from "./bank-accounts.module.css";

type Account = {
  qbo_id: string;
  name: string;
  account_type: string | null;
  acct_num: string | null;
};
type MappedAccount = { qbo_id: string; name: string; active: boolean } | null;
type BankAccount = {
  id: string;
  name: string;
  qbo_account_id: string;
  mapped_account: MappedAccount;
};
type Payload = { bank_accounts: BankAccount[]; accounts: Account[] };

// Where the fetch left us. "ready" carries the payload; the rest are terminal messages.
type View = "loading" | "ready" | "forbidden" | "unauthorized" | "error";

function accountLabel(a: Account): string {
  return a.acct_num ? `${a.name} · ${a.acct_num}` : a.name;
}

// A bank account is "unmapped" (tickets 10/11) once its account is gone or deactivated
// in QuickBooks — the mapping still needs an admin's attention even though it once was valid.
function needsAttention(b: BankAccount): boolean {
  return !b.mapped_account || !b.mapped_account.active;
}

export function BankAccountsAdmin() {
  const [view, setView] = useState<View>("loading");
  const [data, setData] = useState<Payload>({ bank_accounts: [], accounts: [] });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<string | null>(null); // "create" | a bank-account id

  const [name, setName] = useState("");
  const [target, setTarget] = useState(""); // qbo_account_id for the create form

  const apply = useCallback((p: Payload) => {
    setData(p);
    setTarget((t) => t || p.accounts[0]?.qbo_id || "");
  }, []);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/bank-accounts");
      if (res.status === 401) return setView("unauthorized");
      if (res.status === 403) return setView("forbidden");
      if (!res.ok) return setView("error");
      apply(await res.json());
      setView("ready");
    } catch {
      setView("error");
    }
  }, [apply]);

  useEffect(() => {
    load();
  }, [load]);

  async function create(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    setBusy("create");
    try {
      const res = await fetch("/api/bank-accounts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim(), qbo_account_id: target }),
      });
      if (res.status === 422) {
        setError("Give the account a name and map it to an active account.");
        return;
      }
      if (!res.ok) throw new Error(String(res.status));
      apply(await res.json());
      setName("");
    } catch {
      setError("Couldn't add that bank account. Try again.");
    } finally {
      setBusy(null);
    }
  }

  async function remap(b: BankAccount, qbo_account_id: string) {
    setError("");
    setBusy(b.id);
    try {
      const res = await fetch(`/api/bank-accounts/${b.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ qbo_account_id }),
      });
      if (!res.ok) throw new Error(String(res.status));
      setData(await res.json());
    } catch {
      setError("Couldn't change that mapping. Try again.");
    } finally {
      setBusy(null);
    }
  }

  const noTargets = data.accounts.length === 0;

  return (
    <main className={styles.wrap}>
      <Link href="/" className={styles.back}>
        Ledger-Sync
      </Link>
      <h1 className={styles.title}>Bank accounts</h1>
      <p className={styles.intro}>
        Each real-world account you import from — a checking account, a card — tied to the
        chart-of-accounts line its transactions post against. Every entry a bank account
        produces books to the account it maps to, so the mapping is required.
      </p>

      {view === "loading" && <p className={styles.muted}>Loading…</p>}

      {view === "unauthorized" && (
        <div className={styles.state}>
          <p className={styles.stateLead}>Sign in first</p>
          <p className={styles.muted}>
            <Link href="/login" className={styles.inlineLink}>
              Sign in
            </Link>{" "}
            as an admin to set up bank accounts.
          </p>
        </div>
      )}

      {view === "forbidden" && (
        <div className={styles.state}>
          <p className={styles.stateLead}>Admin only</p>
          <p className={styles.muted}>Ask an admin to set up the book&rsquo;s bank accounts.</p>
        </div>
      )}

      {view === "error" && (
        <p className={styles.err} role="alert">
          Couldn&rsquo;t reach the server. Try again.
        </p>
      )}

      {view === "ready" && (
        <>
          {noTargets ? (
            <div className={styles.state}>
              <p className={styles.muted}>
                No active accounts to map to yet.{" "}
                <Link href="/settings/quickbooks" className={styles.inlineLink}>
                  Connect QuickBooks
                </Link>{" "}
                and sync your chart of accounts, then add a bank account here.
              </p>
            </div>
          ) : (
            <form className={styles.create} onSubmit={create}>
              <div className={`${styles.field} ${styles.grow}`}>
                <label htmlFor="ba-name">Bank account</label>
                <input
                  id="ba-name"
                  className={styles.input}
                  type="text"
                  autoComplete="off"
                  required
                  placeholder="Operating checking"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </div>
              <div className={`${styles.field} ${styles.grow}`}>
                <label htmlFor="ba-target">Posts to account</label>
                <select
                  id="ba-target"
                  className={styles.select}
                  value={target}
                  onChange={(e) => setTarget(e.target.value)}
                >
                  {data.accounts.map((a) => (
                    <option key={a.qbo_id} value={a.qbo_id}>
                      {accountLabel(a)}
                    </option>
                  ))}
                </select>
              </div>
              <button className={styles.post} type="submit" disabled={busy === "create"}>
                {busy === "create" ? "Adding…" : "Add"}
              </button>
            </form>
          )}

          <div className={styles.close} aria-hidden="true" />

          {error && (
            <p className={styles.err} role="alert">
              {error}
            </p>
          )}

          <section className={styles.roster} aria-label="Bank accounts">
            {data.bank_accounts.length === 0 ? (
              !noTargets && (
                <p className={styles.muted} style={{ paddingTop: "0.85rem" }}>
                  No bank accounts yet. Add the first one above.
                </p>
              )
            ) : (
              data.bank_accounts.map((b) => {
                const pending = busy === b.id;
                const broken = needsAttention(b);
                return (
                  <div key={b.id} className={styles.entry}>
                    <div className={styles.line}>
                      <span className={styles.who}>{b.name}</span>
                      <span className={styles.leader} aria-hidden="true" />
                      <select
                        className={styles.select}
                        aria-label={`Account for ${b.name}`}
                        value={data.accounts.some((a) => a.qbo_id === b.qbo_account_id)
                          ? b.qbo_account_id
                          : ""}
                        disabled={pending}
                        onChange={(e) => remap(b, e.target.value)}
                      >
                        {broken && (
                          <option value="" disabled>
                            {b.mapped_account
                              ? `${b.mapped_account.name} (inactive)`
                              : "No active account"}
                          </option>
                        )}
                        {data.accounts.map((a) => (
                          <option key={a.qbo_id} value={a.qbo_id}>
                            {accountLabel(a)}
                          </option>
                        ))}
                      </select>
                    </div>
                    {broken && (
                      <p className={styles.attention}>
                        This account is no longer active in QuickBooks. Re-map it to keep
                        posting.
                      </p>
                    )}
                  </div>
                );
              })
            )}
          </section>
        </>
      )}
    </main>
  );
}
