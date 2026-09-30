"use client";

import Link from "next/link";
import { type ReactNode, useMemo, useState } from "react";

import { Nav } from "../components/Nav";
import {
  type Account,
  type PushSummary,
  type ReviewRow,
  type ReviewState,
  type Targets,
  useReview,
} from "../lib/review";
import styles from "./review.module.css";

const TABS: { key: ReviewState | "all"; label: string }[] = [
  { key: "all", label: "All" },
  { key: "uncategorized", label: "Uncategorized" },
  { key: "categorized", label: "Categorized" },
  { key: "approved", label: "Approved" },
  { key: "posted", label: "Posted" },
  { key: "failed", label: "Failed" },
];

// Rows a checkbox can act on: categorized → approve; approved/failed → push.
const SELECTABLE: ReviewState[] = ["categorized", "approved", "failed"];

function accountName(targets: Targets, qboId: string | null): string | null {
  if (!qboId) return null;
  for (const group of Object.values(targets)) {
    const hit = group.find((a) => a.qbo_id === qboId);
    if (hit) return hit.name;
  }
  return null;
}

function formatAmount(amount: string): { text: string; out: boolean } {
  const n = Number(amount);
  const abs = Math.abs(n).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  return n < 0 ? { text: `(${abs})`, out: true } : { text: abs, out: false };
}

export function LedgerReview({ switcher }: { switcher?: ReactNode }) {
  const { view, data, reload, run, ops } = useReview();
  const [tab, setTab] = useState<ReviewState | "all">("all");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [message, setMessage] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const rows = data.transactions;
  const counts = useMemo(() => {
    const c: Record<string, number> = { all: rows.length };
    for (const r of rows) c[r.state] = (c[r.state] ?? 0) + 1;
    return c;
  }, [rows]);

  const shown = tab === "all" ? rows : rows.filter((r) => r.state === tab);

  function switchTab(next: ReviewState | "all") {
    setTab(next);
    setSelected(new Set()); // selection is per-view; don't carry it across filters
  }

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const eligible = shown.filter((r) => SELECTABLE.includes(r.state));
  const allEligibleSelected = eligible.length > 0 && eligible.every((r) => selected.has(r.id));
  function toggleAll() {
    setSelected(allEligibleSelected ? new Set() : new Set(eligible.map((r) => r.id)));
  }

  const selectedRows = rows.filter((r) => selected.has(r.id));
  const toApprove = selectedRows.filter((r) => r.state === "categorized");
  const toPush = selectedRows.filter((r) => r.state === "approved" || r.state === "failed");

  async function withBusy(work: () => Promise<void>) {
    setBusy(true);
    setMessage(null);
    try {
      await work();
    } finally {
      setBusy(false);
    }
  }

  async function onAssign(row: ReviewRow, qboId: string) {
    await withBusy(async () => {
      const res = qboId
        ? await run(() => ops.assignAccount(row.id, qboId))
        : await run(() => ops.clearAccount(row.id));
      if (!res.ok) setMessage({ kind: "err", text: res.error ?? "Couldn't save the account." });
    });
  }

  async function onApprove(row: ReviewRow) {
    await withBusy(async () => {
      const res = await run(() => ops.approve(row.id));
      if (!res.ok) setMessage({ kind: "err", text: res.error ?? "Couldn't approve." });
    });
  }

  async function onUnapprove(row: ReviewRow) {
    if (!row.journal_entry) return;
    await withBusy(async () => {
      const res = await run(() => ops.unapprove(row.journal_entry!.id));
      if (!res.ok) setMessage({ kind: "err", text: res.error ?? "Couldn't un-approve." });
    });
  }

  async function onRetry(row: ReviewRow) {
    if (!row.journal_entry) return;
    await withBusy(async () => {
      const res = await run(() => ops.retry(row.journal_entry!.id));
      if (!res.ok) setMessage({ kind: "err", text: res.error ?? "Retry didn't go through." });
    });
  }

  async function bulkApprove() {
    await withBusy(async () => {
      let ok = 0;
      let firstErr = "";
      for (const r of toApprove) {
        const res = await ops.approve(r.id);
        if (res.ok) ok += 1;
        else if (!firstErr) firstErr = res.error ?? "";
      }
      await reload();
      setSelected(new Set());
      setMessage(
        firstErr
          ? { kind: "err", text: `Approved ${ok}. Some couldn't be approved: ${firstErr}` }
          : { kind: "ok", text: `Approved ${ok} ${ok === 1 ? "entry" : "entries"}.` },
      );
    });
  }

  async function bulkPush() {
    await withBusy(async () => {
      let posted = 0;
      let failed = 0;
      let throttled = false;
      for (const r of toPush) {
        if (!r.journal_entry) continue;
        const res = await ops.retry(r.journal_entry.id);
        const je = res.data as { sync_status?: string; last_error_code?: string } | undefined;
        if (je?.last_error_code === "throttled") throttled = true;
        else if (je?.sync_status === "posted") posted += 1;
        else if (je?.sync_status === "failed") failed += 1;
      }
      await reload();
      setSelected(new Set());
      setMessage(
        throttled
          ? { kind: "err", text: "Rate limited by QuickBooks — retry shortly." }
          : {
              kind: failed ? "err" : "ok",
              text: `Pushed ${posted}${failed ? ` · ${failed} failed` : ""}.`,
            },
      );
    });
  }

  return (
    <main className={styles.wrap}>
      <Nav />
      <header className={styles.masthead}>
        <h1 className={styles.wordmark}>Ledger-Sync</h1>
        <p className={styles.statement}>
          Review the books: assign an account, approve the entry, push it to QuickBooks.
        </p>
      </header>

      {switcher}

      {view === "loading" && <p className={styles.muted}>Loading the ledger…</p>}

      {view === "unauthorized" && (
        <div className={styles.state}>
          <p className={styles.stateLead}>Sign in first</p>
          <p className={styles.muted}>
            <Link href="/login" className={styles.inlineLink}>
              Sign in
            </Link>{" "}
            to review transactions.
          </p>
        </div>
      )}

      {view === "error" && (
        <p className={styles.err} role="alert">
          Couldn&rsquo;t reach the server. Try again.
        </p>
      )}

      {view === "ready" && rows.length === 0 && (
        <div className={styles.state}>
          <p className={styles.stateLead}>No transactions yet</p>
          <p className={styles.muted}>
            <Link href="/import" className={styles.inlineLink}>
              Import a bank statement
            </Link>{" "}
            to start reviewing.
          </p>
        </div>
      )}

      {view === "ready" && rows.length > 0 && (
        <>
          <div className={styles.tabs} role="tablist" aria-label="Filter by state">
            {TABS.map((t) => (
              <button
                key={t.key}
                type="button"
                role="tab"
                aria-selected={tab === t.key}
                className={`${styles.tab} ${tab === t.key ? styles.tabActive : ""}`}
                onClick={() => switchTab(t.key)}
              >
                {t.label}
                <span className={styles.tabCount}>{counts[t.key] ?? 0}</span>
              </button>
            ))}
          </div>

          <div className={styles.bulkBar}>
            <button
              type="button"
              className={styles.bulk}
              onClick={bulkApprove}
              disabled={busy || toApprove.length === 0}
            >
              Approve selected{toApprove.length ? ` (${toApprove.length})` : ""}
            </button>
            <button
              type="button"
              className={styles.bulk}
              onClick={bulkPush}
              disabled={busy || toPush.length === 0}
            >
              Push selected{toPush.length ? ` (${toPush.length})` : ""}
            </button>
            {message && (
              <span
                className={message.kind === "err" ? styles.msgErr : styles.msgOk}
                role="status"
                aria-live="polite"
              >
                {message.text}
              </span>
            )}
          </div>

          <table className={styles.table}>
            <caption className={styles.srOnly}>Transactions to review</caption>
            <thead>
              <tr>
                <th scope="col" className={styles.checkCol}>
                  <input
                    type="checkbox"
                    aria-label="Select all actionable rows"
                    checked={allEligibleSelected}
                    onChange={toggleAll}
                    disabled={eligible.length === 0}
                  />
                </th>
                <th scope="col">Date</th>
                <th scope="col">Detail</th>
                <th scope="col">Account</th>
                <th scope="col" className={styles.amountCol}>
                  Amount
                </th>
                <th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((row) => (
                <Row
                  key={row.id}
                  row={row}
                  targets={data.targets}
                  selected={selected.has(row.id)}
                  busy={busy}
                  onToggle={() => toggle(row.id)}
                  onAssign={(q) => onAssign(row, q)}
                  onApprove={() => onApprove(row)}
                  onUnapprove={() => onUnapprove(row)}
                  onRetry={() => onRetry(row)}
                />
              ))}
            </tbody>
          </table>

          <div className={styles.close} aria-hidden="true" />
          {shown.length === 0 && <p className={styles.muted}>Nothing in this state.</p>}
        </>
      )}
    </main>
  );
}

function Row({
  row,
  targets,
  selected,
  busy,
  onToggle,
  onAssign,
  onApprove,
  onUnapprove,
  onRetry,
}: {
  row: ReviewRow;
  targets: Targets;
  selected: boolean;
  busy: boolean;
  onToggle: () => void;
  onAssign: (qboId: string) => void;
  onApprove: () => void;
  onUnapprove: () => void;
  onRetry: () => void;
}) {
  const amount = formatAmount(row.amount);
  const mutable = row.state === "uncategorized" || row.state === "categorized";
  const selectable = SELECTABLE.includes(row.state);
  const je = row.journal_entry;
  const throttled = je?.last_error_code === "throttled";

  return (
    <tr className={styles[`row_${row.state}`]}>
      <td className={styles.checkCol}>
        {selectable && (
          <input
            type="checkbox"
            aria-label={`Select ${row.description}`}
            checked={selected}
            onChange={onToggle}
          />
        )}
      </td>
      <td className={styles.date}>{row.date}</td>
      <td className={styles.detail}>
        <span className={styles.desc}>{row.description}</span>
        {row.payee && <span className={styles.payee}>{row.payee}</span>}
      </td>
      <td className={styles.account}>
        {mutable ? (
          <select
            className={styles.picker}
            aria-label={`Account for ${row.description}`}
            value={row.assigned_account_qbo_id ?? ""}
            disabled={busy}
            onChange={(e) => onAssign(e.target.value)}
          >
            <option value="">— assign account —</option>
            {Object.entries(targets).map(([group, accounts]) => (
              <optgroup key={group} label={group}>
                {(accounts as Account[]).map((a) => (
                  <option key={a.qbo_id} value={a.qbo_id}>
                    {a.name}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
        ) : (
          <span className={styles.accountFixed}>
            {accountName(targets, row.assigned_account_qbo_id) ??
              row.assigned_account_qbo_id ??
              "—"}
          </span>
        )}
      </td>
      <td className={`${styles.amountCol} ${amount.out ? styles.out : styles.in}`}>{amount.text}</td>
      <td className={styles.status}>
        {row.state === "uncategorized" && (
          <button
            type="button"
            className={styles.action}
            onClick={onApprove}
            disabled
            title="Assign an account before approving"
            aria-label="Approve — assign an account first"
          >
            Approve
          </button>
        )}
        {row.state === "categorized" && (
          <button
            type="button"
            className={styles.action}
            onClick={onApprove}
            disabled={busy}
          >
            Approve
          </button>
        )}
        {row.state === "approved" && (
          <span className={styles.statusWrap}>
            <span className={styles.badgeApproved}>
              Approved{je?.doc_number ? ` · #${je.doc_number}` : ""}
            </span>
            {throttled && <span className={styles.retryShortly}>Rate limited — retry shortly</span>}
            <button type="button" className={styles.linkAction} onClick={onUnapprove} disabled={busy}>
              Un-approve
            </button>
          </span>
        )}
        {row.state === "posted" && (
          <span className={styles.badgePosted}>Posted{je?.doc_number ? ` · #${je.doc_number}` : ""}</span>
        )}
        {row.state === "failed" && (
          <span className={styles.statusWrap}>
            <span className={styles.badgeFailed} title={je?.last_error ?? ""}>
              Failed
            </span>
            {je?.last_error && <span className={styles.errorText}>{je.last_error}</span>}
            <button type="button" className={styles.action} onClick={onRetry} disabled={busy}>
              Retry
            </button>
          </span>
        )}
      </td>
    </tr>
  );
}
