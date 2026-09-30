"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import styles from "./import.module.css";

type Profile = { id: string; name: string; bank_account_id: string | null; config: unknown };
type BankAccount = { id: string; name: string; qbo_account_id: string };
type Catalog = { profiles: Profile[]; bank_accounts: BankAccount[] };
type Preview = Catalog & { header: string[]; suggested_profile_id: string | null };
type Rejected = { raw: Record<string, string>; reasons: string[] };
type Summary = {
  imported: number;
  duplicates: number;
  in_file_duplicate_count: number;
  rejected: Rejected[];
};

type View = "loading" | "ready" | "unauthorized" | "error";

export function ImportReview() {
  const [view, setView] = useState<View>("loading");
  const [catalog, setCatalog] = useState<Catalog>({ profiles: [], bank_accounts: [] });
  const [error, setError] = useState("");

  const [fileName, setFileName] = useState("");
  const [csv, setCsv] = useState<string | null>(null);
  const [suggestedId, setSuggestedId] = useState<string | null>(null);
  const [profileId, setProfileId] = useState("");
  const [bankId, setBankId] = useState("");

  const [busy, setBusy] = useState<"preview" | "import" | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/csv-import/profiles");
      if (res.status === 401) return setView("unauthorized");
      if (!res.ok) return setView("error");
      setCatalog(await res.json());
      setView("ready");
    } catch {
      setView("error");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Default the source account to the chosen profile's default, else the first.
  function pickBankFor(id: string, banks: BankAccount[], profiles: Profile[]) {
    const profile = profiles.find((p) => p.id === id);
    return profile?.bank_account_id ?? banks[0]?.id ?? "";
  }

  async function onFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setError("");
    setSummary(null);
    setFileName(file.name);
    setBusy("preview");
    try {
      const text = await file.text();
      setCsv(text);
      const res = await fetch("/api/csv-import/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ csv: text }),
      });
      if (res.status === 401) return setView("unauthorized");
      if (!res.ok) throw new Error(String(res.status));
      const p: Preview = await res.json();
      setCatalog({ profiles: p.profiles, bank_accounts: p.bank_accounts });
      setSuggestedId(p.suggested_profile_id);
      const chosen = p.suggested_profile_id ?? p.profiles[0]?.id ?? "";
      setProfileId(chosen);
      setBankId(pickBankFor(chosen, p.bank_accounts, p.profiles));
    } catch {
      setError("Couldn't read that file. Check it's a CSV export and try again.");
      setCsv(null);
    } finally {
      setBusy(null);
    }
  }

  function onProfileChange(id: string) {
    setProfileId(id);
    setBankId(pickBankFor(id, catalog.bank_accounts, catalog.profiles));
  }

  async function runImport() {
    if (!csv || !profileId || !bankId) return;
    setError("");
    setBusy("import");
    try {
      const res = await fetch("/api/csv-import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ csv, profile_id: profileId, bank_account_id: bankId }),
      });
      if (res.status === 401) return setView("unauthorized");
      if (!res.ok) throw new Error(String(res.status));
      setSummary(await res.json());
    } catch {
      setError("The import didn't go through. Nothing was saved — try again.");
    } finally {
      setBusy(null);
    }
  }

  const noBanks = catalog.bank_accounts.length === 0;
  const noProfiles = catalog.profiles.length === 0;
  const ready = csv != null && profileId !== "" && bankId !== "";

  return (
    <main className={styles.wrap}>
      <Link href="/" className={styles.back}>
        Ledger-Sync
      </Link>
      <h1 className={styles.title}>Import transactions</h1>
      <p className={styles.intro}>
        Bring in a bank&rsquo;s CSV export. Ledger-Sync reads it with a saved column mapping,
        keeps every genuine transaction, and skips anything you&rsquo;ve already imported into
        the same account.
      </p>

      {view === "loading" && <p className={styles.muted}>Loading…</p>}

      {view === "unauthorized" && (
        <div className={styles.state}>
          <p className={styles.stateLead}>Sign in first</p>
          <p className={styles.muted}>
            <Link href="/login" className={styles.inlineLink}>
              Sign in
            </Link>{" "}
            to import transactions.
          </p>
        </div>
      )}

      {view === "error" && (
        <p className={styles.err} role="alert">
          Couldn&rsquo;t reach the server. Try again.
        </p>
      )}

      {view === "ready" && (
        <>
          {noBanks ? (
            <div className={styles.state}>
              <p className={styles.stateLead}>No bank accounts yet</p>
              <p className={styles.muted}>
                Every import posts to a bank account.{" "}
                <Link href="/admin/bank-accounts" className={styles.inlineLink}>
                  An admin sets those up
                </Link>{" "}
                — once one exists, come back to import.
              </p>
            </div>
          ) : noProfiles ? (
            <div className={styles.state}>
              <p className={styles.stateLead}>No column mappings yet</p>
              <p className={styles.muted}>
                A column mapping tells Ledger-Sync which columns hold the date, amount, and
                description for a given bank. Add one before importing.
              </p>
            </div>
          ) : (
            <>
              <div className={styles.pickup}>
                <label className={styles.fileField}>
                  <span className={styles.fileLabel}>Statement file</span>
                  <span className={styles.fileControl}>
                    <span className={styles.fileButton}>Choose CSV</span>
                    <span className={styles.fileName}>
                      {fileName || "No file chosen"}
                      {busy === "preview" && " · reading…"}
                    </span>
                    <input
                      type="file"
                      accept=".csv,text/csv"
                      className={styles.fileInput}
                      onChange={onFile}
                    />
                  </span>
                </label>
              </div>

              {csv != null && (
                <section className={styles.confirm} aria-label="Confirm mapping and account">
                  <div className={styles.field}>
                    <label htmlFor="imp-profile">Column mapping</label>
                    <select
                      id="imp-profile"
                      className={styles.select}
                      value={profileId}
                      onChange={(e) => onProfileChange(e.target.value)}
                    >
                      {catalog.profiles.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name}
                          {p.id === suggestedId ? " — matches this file" : ""}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className={styles.field}>
                    <label htmlFor="imp-bank">Posts to bank account</label>
                    <select
                      id="imp-bank"
                      className={styles.select}
                      value={bankId}
                      onChange={(e) => setBankId(e.target.value)}
                    >
                      {catalog.bank_accounts.map((b) => (
                        <option key={b.id} value={b.id}>
                          {b.name}
                        </option>
                      ))}
                    </select>
                  </div>
                </section>
              )}

              <div className={styles.close} aria-hidden="true" />

              <button
                className={styles.post}
                type="button"
                onClick={runImport}
                disabled={!ready || busy === "import"}
              >
                {busy === "import" ? "Importing…" : "Import transactions"}
              </button>

              {error && (
                <p className={styles.err} role="alert">
                  {error}
                </p>
              )}

              {summary && <Result summary={summary} />}
            </>
          )}
        </>
      )}
    </main>
  );
}

function Result({ summary }: { summary: Summary }) {
  return (
    <section className={styles.result} aria-label="Import result" aria-live="polite">
      <h2 className={styles.resultHead}>This import</h2>
      <dl className={styles.ledger}>
        <Row label="Imported" value={summary.imported} />
        <Row label="Already imported — skipped" value={summary.duplicates} muted />
        <Row
          label="Possible duplicates within this file"
          value={summary.in_file_duplicate_count}
          hint="kept for you to review, not skipped"
          muted={summary.in_file_duplicate_count === 0}
        />
        <Row
          label="Rejected"
          value={summary.rejected.length}
          flag={summary.rejected.length > 0}
        />
      </dl>
      <div className={styles.ledgerClose} aria-hidden="true" />

      {summary.rejected.length > 0 && (
        <div className={styles.rejects}>
          <p className={styles.rejectsLead}>
            These rows weren&rsquo;t imported. Fix them at the source and import again.
          </p>
          <ul className={styles.rejectList}>
            {summary.rejected.map((r, i) => (
              <li key={i} className={styles.reject}>
                <span className={styles.rejectReasons}>{r.reasons.join(", ")}</span>
                <span className={styles.rejectRaw}>{Object.values(r.raw).join(" · ")}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function Row({
  label,
  value,
  hint,
  muted,
  flag,
}: {
  label: string;
  value: number;
  hint?: string;
  muted?: boolean;
  flag?: boolean;
}) {
  return (
    <div className={styles.row}>
      <dt className={styles.rowLabel}>
        {label}
        {hint && <span className={styles.rowHint}>{hint}</span>}
      </dt>
      <span className={styles.leader} aria-hidden="true" />
      <dd
        className={`${styles.rowValue} ${muted ? styles.zero : ""} ${flag ? styles.flagged : ""}`}
      >
        {value}
      </dd>
    </div>
  );
}
