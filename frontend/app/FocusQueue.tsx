"use client";

/**
 * Focus queue — one transaction at a time, read as a journal entry: the two
 * debit/credit lines the backend previews, closed by the double rule, with the
 * balance proved underneath. Keyboard-first so the money path can be walked
 * quickly: ←/→ (or j/k) to move, A (or Enter) to approve.
 *
 * Same data and same four operations as every review view — both come from
 * `@/lib/review`; this is a layout, not a different feature.
 */

import Link from "next/link";
import { type ReactNode, useCallback, useEffect, useState } from "react";

import { Nav } from "../components/Nav";
import { accountName, formatAmount, type PreviewData, useReview } from "../lib/review";
import shell from "./review.module.css";
import styles from "./focus.module.css";

function money(amount: string): string {
  return Number(amount).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export function FocusQueue({ switcher }: { switcher?: ReactNode }) {
  const { view, data, run, ops } = useReview();
  const [idx, setIdx] = useState(0);
  const [preview, setPreview] = useState<PreviewData | null>(null);
  const [previewErr, setPreviewErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  const rows = data.transactions;
  const safeIdx = Math.min(idx, Math.max(0, rows.length - 1));
  const current = rows[safeIdx];
  const canApprove = !!current && current.state === "categorized" && !busy;

  // Preview the two lines whenever the transaction (or its account) changes.
  // `ignore` guards against an out-of-order response landing on a later card.
  useEffect(() => {
    if (!current || !current.assigned_account_qbo_id) {
      setPreview(null);
      setPreviewErr(null);
      return;
    }
    let ignore = false;
    setPreview(null);
    setPreviewErr(null);
    ops.preview(current.id).then((res) => {
      if (ignore) return;
      if (res.ok && res.data) setPreview(res.data);
      else setPreviewErr(res.error ?? "Couldn't build the preview.");
    });
    return () => {
      ignore = true;
    };
  }, [current?.id, current?.assigned_account_qbo_id, current?.state, ops, current]);

  async function withBusy(work: () => Promise<void>) {
    setBusy(true);
    setMessage(null);
    try {
      await work();
    } finally {
      setBusy(false);
    }
  }

  const approveCurrent = useCallback(async () => {
    if (!current || current.state !== "categorized") return;
    setBusy(true);
    setMessage(null);
    const res = await run(() => ops.approve(current.id));
    setBusy(false);
    if (!res.ok) setMessage({ kind: "err", text: res.error ?? "Couldn't approve." });
  }, [current, run, ops]);

  // Keyboard-first navigation + approval. Ignore keys typed into the account
  // picker and any modified chords so we never fight native controls.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const t = e.target as HTMLElement | null;
      // Let native controls handle their own keys (Enter on a focused button
      // already clicks it — don't also fire our handler).
      if (t && ["INPUT", "SELECT", "TEXTAREA", "BUTTON"].includes(t.tagName)) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "ArrowRight" || e.key === "ArrowDown" || e.key === "j") {
        e.preventDefault();
        setIdx((i) => Math.min(i + 1, rows.length - 1));
      } else if (e.key === "ArrowLeft" || e.key === "ArrowUp" || e.key === "k") {
        e.preventDefault();
        setIdx((i) => Math.max(i - 1, 0));
      } else if (e.key === "a" || e.key === "Enter") {
        if (canApprove) {
          e.preventDefault();
          void approveCurrent();
        }
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [rows.length, canApprove, approveCurrent]);

  async function onAssign(qboId: string) {
    if (!current) return;
    await withBusy(async () => {
      const res = qboId
        ? await run(() => ops.assignAccount(current.id, qboId))
        : await run(() => ops.clearAccount(current.id));
      if (!res.ok) setMessage({ kind: "err", text: res.error ?? "Couldn't save the account." });
    });
  }

  async function onUnapprove() {
    if (!current?.journal_entry) return;
    await withBusy(async () => {
      const res = await run(() => ops.unapprove(current.journal_entry!.id));
      if (!res.ok) setMessage({ kind: "err", text: res.error ?? "Couldn't un-approve." });
    });
  }

  async function onRetry() {
    if (!current?.journal_entry) return;
    await withBusy(async () => {
      const res = await run(() => ops.retry(current.journal_entry!.id));
      if (!res.ok) setMessage({ kind: "err", text: res.error ?? "Retry didn't go through." });
    });
  }

  const amt = current ? formatAmount(current.amount) : null;
  const mutable = current?.state === "uncategorized" || current?.state === "categorized";
  const je = current?.journal_entry;

  return (
    <main className={shell.wrap}>
      <Nav />
      <header className={shell.masthead}>
        <h1 className={shell.wordmark}>Ledger-Sync</h1>
        <p className={shell.statement}>
          Focus on one entry: prove it balances, then approve. Use ← → to move, A to approve.
        </p>
      </header>

      {switcher}

      {view === "loading" && <p className={shell.muted}>Loading the ledger…</p>}

      {view === "unauthorized" && (
        <div className={shell.state}>
          <p className={shell.stateLead}>Sign in first</p>
          <p className={shell.muted}>
            <Link href="/login" className={shell.inlineLink}>
              Sign in
            </Link>{" "}
            to review transactions.
          </p>
        </div>
      )}

      {view === "error" && (
        <p className={shell.err} role="alert">
          Couldn&rsquo;t reach the server. Try again.
        </p>
      )}

      {view === "ready" && rows.length === 0 && (
        <div className={shell.state}>
          <p className={shell.stateLead}>No transactions yet</p>
          <p className={shell.muted}>
            <Link href="/import" className={shell.inlineLink}>
              Import a bank statement
            </Link>{" "}
            to start reviewing.
          </p>
        </div>
      )}

      {view === "ready" && current && amt && (
        <section className={styles.card} aria-label="Transaction in focus">
          <div className={styles.head}>
            <span className={styles.pos} aria-label={`Transaction ${safeIdx + 1} of ${rows.length}`}>
              {safeIdx + 1} <span className={styles.posOf}>of</span> {rows.length}
            </span>
            <span className={styles.date}>{current.date}</span>
          </div>

          <h2 className={styles.desc}>{current.description}</h2>
          {current.payee && <p className={styles.payee}>{current.payee}</p>}
          <p className={`${styles.amount} ${amt.out ? styles.out : styles.in}`}>{amt.text}</p>

          <div className={styles.accountRow}>
            {mutable ? (
              <label className={styles.accountLabel}>
                Account
                <select
                  className={styles.picker}
                  aria-label={`Account for ${current.description}`}
                  value={current.assigned_account_qbo_id ?? ""}
                  disabled={busy}
                  onChange={(e) => onAssign(e.target.value)}
                >
                  <option value="">— assign account —</option>
                  {Object.entries(data.targets).map(([group, accounts]) => (
                    <optgroup key={group} label={group}>
                      {accounts.map((a) => (
                        <option key={a.qbo_id} value={a.qbo_id}>
                          {a.name}
                        </option>
                      ))}
                    </optgroup>
                  ))}
                </select>
              </label>
            ) : (
              <span className={styles.accountFixed}>
                {accountName(data.targets, current.assigned_account_qbo_id) ??
                  current.assigned_account_qbo_id ??
                  "—"}
              </span>
            )}
          </div>

          {/* The journal entry preview: two lines, closed, then proved. */}
          {preview && (
            <div className={styles.entry}>
              <dl className={styles.lines}>
                {preview.lines.map((ln) => (
                  <div className={styles.line} key={ln.posting_type}>
                    <dt className={styles.side}>{ln.posting_type}</dt>
                    <dd className={styles.acct}>
                      {accountName(data.targets, ln.account_id) ?? ln.account_id}
                    </dd>
                    <span className={styles.leader} aria-hidden="true" />
                    <span className={styles.lineAmt}>{money(ln.amount)}</span>
                  </div>
                ))}
              </dl>
              <div className={styles.close} aria-hidden="true" />
              <p
                className={preview.balanced ? styles.balanced : styles.unbalanced}
                role="status"
              >
                {preview.balanced
                  ? `Balanced — debits ${money(preview.debit_total)} = credits ${money(preview.credit_total)}`
                  : `Out of balance — debits ${money(preview.debit_total)} ≠ credits ${money(preview.credit_total)}`}
              </p>
            </div>
          )}
          {previewErr && (
            <p className={styles.previewErr} role="status">
              {previewErr}
            </p>
          )}
          {!preview && !previewErr && !current.assigned_account_qbo_id && (
            <p className={styles.previewHint}>Assign an account to preview the entry.</p>
          )}

          {/* Actions mirror the table exactly — the same operations. */}
          <div className={styles.actions}>
            {current.state === "categorized" && (
              <button
                type="button"
                className={styles.approve}
                onClick={approveCurrent}
                disabled={busy}
              >
                Approve <kbd className={styles.kbd}>A</kbd>
              </button>
            )}
            {current.state === "uncategorized" && (
              <span className={styles.blocked}>Assign an account before approving.</span>
            )}
            {current.state === "approved" && (
              <span className={styles.statusRow}>
                <span className={styles.badgeApproved}>
                  Approved{je?.doc_number ? ` · #${je.doc_number}` : ""}
                </span>
                <button
                  type="button"
                  className={styles.linkAction}
                  onClick={onUnapprove}
                  disabled={busy}
                >
                  Un-approve
                </button>
              </span>
            )}
            {current.state === "posted" && (
              <span className={styles.badgePosted}>
                Posted{je?.doc_number ? ` · #${je.doc_number}` : ""}
              </span>
            )}
            {current.state === "failed" && (
              <span className={styles.statusRow}>
                <span className={styles.badgeFailed} title={je?.last_error ?? ""}>
                  Failed
                </span>
                {je?.last_error && <span className={styles.errorText}>{je.last_error}</span>}
                <button type="button" className={styles.approve} onClick={onRetry} disabled={busy}>
                  Retry
                </button>
              </span>
            )}
            {message && (
              <span
                className={message.kind === "err" ? shell.msgErr : shell.msgOk}
                role="status"
                aria-live="polite"
              >
                {message.text}
              </span>
            )}
          </div>

          <div className={styles.nav}>
            <button
              type="button"
              className={styles.step}
              onClick={() => setIdx((i) => Math.max(i - 1, 0))}
              disabled={safeIdx === 0}
            >
              ← Previous
            </button>
            <button
              type="button"
              className={styles.step}
              onClick={() => setIdx((i) => Math.min(i + 1, rows.length - 1))}
              disabled={safeIdx >= rows.length - 1}
            >
              Next →
            </button>
          </div>
        </section>
      )}
    </main>
  );
}
