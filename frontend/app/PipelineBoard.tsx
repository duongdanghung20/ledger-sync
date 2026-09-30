"use client";

/**
 * Pipeline board — a lane per state, worked as a state machine:
 * uncategorized → categorized → approved → posted, with failed alongside.
 * A card advances one step (assign / approve / push / retry) and a lane can be
 * advanced whole. Same data and same four operations as every review view —
 * both come from `@/lib/review`; this is a layout, not a different feature.
 */

import Link from "next/link";
import { type ReactNode, useMemo, useState } from "react";

import { Nav } from "../components/Nav";
import {
  accountName,
  formatAmount,
  type ReviewRow,
  type ReviewState,
  useReview,
} from "../lib/review";
import shell from "./review.module.css";
import styles from "./pipeline.module.css";

const LANES: { state: ReviewState; label: string }[] = [
  { state: "uncategorized", label: "Uncategorized" },
  { state: "categorized", label: "Categorized" },
  { state: "approved", label: "Approved" },
  { state: "posted", label: "Posted" },
  { state: "failed", label: "Failed" },
];

// The per-card step label for a lane, or null where a card can't be advanced
// with a single click (uncategorized needs an account chosen; posted is done).
function cardAdvanceLabel(state: ReviewState): string | null {
  if (state === "categorized") return "Approve";
  if (state === "approved") return "Push";
  if (state === "failed") return "Retry";
  return null;
}

export function PipelineBoard({ switcher }: { switcher?: ReactNode }) {
  const { view, data, reload, run, ops } = useReview();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  const rows = data.transactions;
  const byState = useMemo(() => {
    const m: Record<string, ReviewRow[]> = {};
    for (const l of LANES) m[l.state] = [];
    for (const r of rows) (m[r.state] ??= []).push(r);
    return m;
  }, [rows]);

  async function withBusy(work: () => Promise<void>) {
    setBusy(true);
    setMessage(null);
    try {
      await work();
    } finally {
      setBusy(false);
    }
  }

  async function assign(card: ReviewRow, qboId: string) {
    await withBusy(async () => {
      const res = qboId
        ? await run(() => ops.assignAccount(card.id, qboId))
        : await run(() => ops.clearAccount(card.id));
      if (!res.ok) setMessage({ kind: "err", text: res.error ?? "Couldn't save the account." });
    });
  }

  async function advanceCard(card: ReviewRow) {
    await withBusy(async () => {
      let res;
      if (card.state === "categorized") res = await run(() => ops.approve(card.id));
      else if (card.journal_entry) res = await run(() => ops.retry(card.journal_entry!.id));
      else return;
      if (!res.ok) setMessage({ kind: "err", text: res.error ?? "Couldn't advance the card." });
    });
  }

  async function advanceLane(state: ReviewState) {
    const cards = byState[state] ?? [];
    await withBusy(async () => {
      if (state === "categorized") {
        let ok = 0;
        let firstErr = "";
        for (const c of cards) {
          const res = await ops.approve(c.id);
          if (res.ok) ok += 1;
          else if (!firstErr) firstErr = res.error ?? "";
        }
        await reload();
        setMessage(
          firstErr
            ? { kind: "err", text: `Approved ${ok}. Some couldn't be approved: ${firstErr}` }
            : { kind: "ok", text: `Approved ${ok} ${ok === 1 ? "entry" : "entries"}.` },
        );
      } else if (state === "approved") {
        // Push every pending entry in one call — the real bulk push operation.
        const res = await run(() => ops.pushAll());
        const s = res.data as
          | { posted?: number; failed?: number; throttled?: boolean }
          | undefined;
        setMessage(
          !res.ok
            ? { kind: "err", text: res.error ?? "Couldn't push." }
            : s?.throttled
              ? { kind: "err", text: "Rate limited by QuickBooks — retry shortly." }
              : {
                  kind: s?.failed ? "err" : "ok",
                  text: `Pushed ${s?.posted ?? 0}${s?.failed ? ` · ${s.failed} failed` : ""}.`,
                },
        );
      } else if (state === "failed") {
        let posted = 0;
        let failed = 0;
        let throttled = false;
        for (const c of cards) {
          if (!c.journal_entry) continue;
          const res = await ops.retry(c.journal_entry.id);
          const je = res.data as { sync_status?: string; last_error_code?: string } | undefined;
          if (je?.last_error_code === "throttled") throttled = true;
          else if (je?.sync_status === "posted") posted += 1;
          else if (je?.sync_status === "failed") failed += 1;
        }
        await reload();
        setMessage(
          throttled
            ? { kind: "err", text: "Rate limited by QuickBooks — retry shortly." }
            : { kind: failed ? "err" : "ok", text: `Pushed ${posted}${failed ? ` · ${failed} failed` : ""}.` },
        );
      }
    });
  }

  function laneActionLabel(state: ReviewState): string | null {
    if (state === "categorized") return "Approve all";
    if (state === "approved") return "Push all";
    if (state === "failed") return "Retry all";
    return null;
  }

  return (
    <main className={styles.wrap}>
      <Nav />
      <header className={shell.masthead}>
        <h1 className={shell.wordmark}>Ledger-Sync</h1>
        <p className={shell.statement}>
          The queue as a pipeline: advance a card, or a whole lane, from left to right.
        </p>
      </header>

      {switcher}

      {message && (
        <p
          className={message.kind === "err" ? shell.msgErr : shell.msgOk}
          role="status"
          aria-live="polite"
        >
          {message.text}
        </p>
      )}

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

      {view === "ready" && rows.length > 0 && (
        <div className={styles.board}>
          {LANES.map((lane) => {
            const cards = byState[lane.state] ?? [];
            const actionLabel = laneActionLabel(lane.state);
            return (
              <section
                key={lane.state}
                className={`${styles.lane} ${styles[`lane_${lane.state}`] ?? ""}`}
                aria-label={`${lane.label} — ${cards.length}`}
              >
                <div className={styles.laneHead}>
                  <h2 className={styles.laneTitle}>
                    {lane.label}
                    <span className={styles.laneCount}>{cards.length}</span>
                  </h2>
                  {actionLabel && (
                    <button
                      type="button"
                      className={styles.laneAction}
                      onClick={() => advanceLane(lane.state)}
                      disabled={busy || cards.length === 0}
                    >
                      {actionLabel}
                    </button>
                  )}
                </div>

                <ul className={styles.cards}>
                  {cards.map((card) => (
                    <Card
                      key={card.id}
                      card={card}
                      targets={data.targets}
                      busy={busy}
                      onAssign={(q) => assign(card, q)}
                      onAdvance={() => advanceCard(card)}
                    />
                  ))}
                  {cards.length === 0 && <li className={styles.empty}>—</li>}
                </ul>
              </section>
            );
          })}
        </div>
      )}
    </main>
  );
}

function Card({
  card,
  targets,
  busy,
  onAssign,
  onAdvance,
}: {
  card: ReviewRow;
  targets: ReturnType<typeof useReview>["data"]["targets"];
  busy: boolean;
  onAssign: (qboId: string) => void;
  onAdvance: () => void;
}) {
  const amt = formatAmount(card.amount);
  const advance = cardAdvanceLabel(card.state);
  const je = card.journal_entry;

  return (
    <li className={styles.card}>
      <div className={styles.cardTop}>
        <span className={styles.cardDate}>{card.date}</span>
        <span className={`${styles.cardAmt} ${amt.out ? styles.out : styles.in}`}>{amt.text}</span>
      </div>
      <p className={styles.cardDesc}>{card.description}</p>

      {card.state === "uncategorized" ? (
        <select
          className={styles.picker}
          aria-label={`Account for ${card.description}`}
          value={card.assigned_account_qbo_id ?? ""}
          disabled={busy}
          onChange={(e) => onAssign(e.target.value)}
        >
          <option value="">— assign account —</option>
          {Object.entries(targets).map(([group, accounts]) => (
            <optgroup key={group} label={group}>
              {accounts.map((a) => (
                <option key={a.qbo_id} value={a.qbo_id}>
                  {a.name}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
      ) : (
        <p className={styles.cardAccount}>
          {accountName(targets, card.assigned_account_qbo_id) ?? "—"}
        </p>
      )}

      {je?.last_error && card.state === "failed" && (
        <p className={styles.cardError}>{je.last_error}</p>
      )}

      {advance && (
        <button type="button" className={styles.advance} onClick={onAdvance} disabled={busy}>
          {advance} →
        </button>
      )}
    </li>
  );
}
