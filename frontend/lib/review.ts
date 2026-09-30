"use client";

/**
 * Shared review data + operations — the ONE seam every review view uses.
 *
 * Ticket 12's Ledger table and ticket 13's Focus queue / Pipeline board all read
 * the same `/api/review` model and drive the same four operations, so both the
 * fetch and the operations live here once. The operations are thin wrappers over
 * the existing feature endpoints (categorization / journal / push) — nothing is
 * re-implemented; state is DERIVED by the backend, never stored twice.
 *
 * Seam for ticket 13: import `useReview()` for the data + reload + the four
 * operations, or the bare `reviewOps` functions if you manage your own state.
 */

import { useCallback, useEffect, useState } from "react";

export type ReviewState =
  | "uncategorized"
  | "categorized"
  | "approved"
  | "posted"
  | "failed";

export type JournalEntry = {
  id: string;
  sync_status: "pending" | "posted" | "failed";
  doc_number: number | null;
  last_error: string | null;
  last_error_code: string | null;
  attempt_count: number;
};

export type ReviewRow = {
  id: string;
  date: string;
  amount: string; // signed 2dp; negative = money out
  description: string;
  payee: string | null;
  bank_account_id: string;
  assigned_account_qbo_id: string | null;
  category_source: string | null;
  state: ReviewState;
  journal_entry: JournalEntry | null;
};

export type Account = {
  qbo_id: string;
  name: string;
  account_type: string | null;
  classification: string | null;
};

export type Targets = Record<string, Account[]>;

export type PushSummary = {
  posted: number;
  failed: number;
  pending: number;
  throttled: boolean;
};

/** One side of a transaction's journal preview (ticket 10). */
export type PreviewLine = {
  account_id: string;
  posting_type: "Debit" | "Credit";
  amount: string;
};

export type PreviewData = {
  lines: PreviewLine[];
  debit_total: string;
  credit_total: string;
  balanced: boolean;
};

export type OpResult<T = unknown> = {
  ok: boolean;
  status: number;
  error?: string;
  data?: T;
};

async function send<T = unknown>(url: string, init?: RequestInit): Promise<OpResult<T>> {
  try {
    const res = await fetch(url, init);
    let body: unknown = undefined;
    try {
      body = await res.json();
    } catch {
      // no/empty body (e.g. 204) — fine
    }
    if (res.ok) return { ok: true, status: res.status, data: body as T };
    const detail = (body as { detail?: string } | undefined)?.detail;
    return { ok: false, status: res.status, error: detail ?? `Request failed (${res.status})` };
  } catch {
    return { ok: false, status: 0, error: "Couldn't reach the server." };
  }
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

/** The four operations, as plain functions — reusable outside React. */
export const reviewOps = {
  assignAccount: (txId: string, qboAccountId: string) =>
    send(`/api/categorization/transactions/${txId}/assign`, json({ qbo_account_id: qboAccountId })),
  clearAccount: (txId: string) =>
    send(`/api/categorization/transactions/${txId}/category`, { method: "DELETE" }),
  approve: (txId: string) =>
    send(`/api/journal/transactions/${txId}/approve`, { method: "POST" }),
  unapprove: (jeId: string) =>
    send(`/api/journal/entries/${jeId}/unapprove`, { method: "POST" }),
  pushAll: () => send<PushSummary>(`/api/push`, { method: "POST" }),
  retry: (jeId: string) => send(`/api/push/entries/${jeId}/retry`, { method: "POST" }),
  // Read-only: the two debit/credit lines the Focus queue proves balance on.
  preview: (txId: string) => send<PreviewData>(`/api/journal/transactions/${txId}/preview`),
};

export type ReviewData = { transactions: ReviewRow[]; targets: Targets };
export type ReviewView = "loading" | "ready" | "unauthorized" | "error";

/**
 * The shared review hook: `/api/review` data + a reload, plus the four operations
 * wrapped to reload on success and hand back any error for the caller to surface.
 */
export function useReview() {
  const [view, setView] = useState<ReviewView>("loading");
  const [data, setData] = useState<ReviewData>({ transactions: [], targets: {} });

  const reload = useCallback(async () => {
    const res = await send<ReviewData>("/api/review");
    if (res.status === 401) return setView("unauthorized");
    if (!res.ok || !res.data) return setView("error");
    setData(res.data);
    setView("ready");
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  // Each op runs, then reloads on success so the derived state is re-read from
  // the source of truth rather than guessed locally.
  const run = useCallback(
    async (op: () => Promise<OpResult>): Promise<OpResult> => {
      const res = await op();
      if (res.ok) await reload();
      return res;
    },
    [reload],
  );

  return { view, data, reload, run, ops: reviewOps };
}

/** Look up a target account's display name by its QBO id (across all groups). */
export function accountName(targets: Targets, qboId: string | null): string | null {
  if (!qboId) return null;
  for (const group of Object.values(targets)) {
    const hit = group.find((a) => a.qbo_id === qboId);
    if (hit) return hit.name;
  }
  return null;
}

/** Signed 2dp amount → ledger text; money out shown in accounting parentheses. */
export function formatAmount(amount: string): { text: string; out: boolean } {
  const n = Number(amount);
  const abs = Math.abs(n).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  return n < 0 ? { text: `(${abs})`, out: true } : { text: abs, out: false };
}

// ── Remembered review layout ────────────────────────────────────────────────
// Which of the three views the Bookkeeper last used. A preference, so it lives
// in localStorage (zero migration) — never a server field.

export const REVIEW_LAYOUTS = ["ledger", "focus", "pipeline"] as const;
export type ReviewLayout = (typeof REVIEW_LAYOUTS)[number];
const LAYOUT_KEY = "preferred_review_view";

/** Layout choice, restored from localStorage on mount, persisted on change. */
export function usePreferredLayout(): [ReviewLayout, (next: ReviewLayout) => void] {
  const [layout, setLayout] = useState<ReviewLayout>("ledger");

  // Read after mount only: localStorage is client-only, so reading during render
  // would mismatch the server-rendered "ledger" default and warn on hydration.
  useEffect(() => {
    try {
      const stored = localStorage.getItem(LAYOUT_KEY);
      if (stored && (REVIEW_LAYOUTS as readonly string[]).includes(stored)) {
        setLayout(stored as ReviewLayout);
      }
    } catch {
      // storage blocked/unavailable — keep the default
    }
  }, []);

  const choose = useCallback((next: ReviewLayout) => {
    setLayout(next);
    try {
      localStorage.setItem(LAYOUT_KEY, next);
    } catch {
      // storage blocked — the choice still applies for this session
    }
  }, []);

  return [layout, choose];
}
