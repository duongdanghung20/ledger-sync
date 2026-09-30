import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { FocusQueue } from "../app/FocusQueue";
import { PipelineBoard } from "../app/PipelineBoard";
import { ReviewWorkspace } from "../app/ReviewWorkspace";

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

const je = (over: Record<string, unknown>) => ({
  id: "je",
  sync_status: "pending",
  doc_number: null,
  last_error: null,
  last_error_code: null,
  attempt_count: 0,
  ...over,
});

function row(id: string, description: string, state: string, over: Record<string, unknown> = {}) {
  return {
    id,
    date: "2026-09-01",
    amount: "-10.00",
    description,
    payee: null,
    bank_account_id: "ba",
    assigned_account_qbo_id: null,
    category_source: "manual",
    state,
    journal_entry: null,
    ...over,
  };
}

// Categorized first so the Focus queue lands on a previewable, approvable entry.
const review = {
  transactions: [
    row("t-cat", "Paper", "categorized", { assigned_account_qbo_id: "2", payee: "Staples" }),
    row("t-un", "Coffee", "uncategorized", { assigned_account_qbo_id: null }),
    row("t-app", "Rent", "approved", {
      assigned_account_qbo_id: "2",
      journal_entry: je({ id: "je-app", doc_number: 1 }),
    }),
    row("t-post", "Utilities", "posted", {
      assigned_account_qbo_id: "2",
      journal_entry: je({ id: "je-post", sync_status: "posted", doc_number: 2 }),
    }),
    row("t-fail", "Software", "failed", {
      assigned_account_qbo_id: "2",
      journal_entry: je({
        id: "je-fail",
        sync_status: "failed",
        doc_number: 3,
        last_error: "QuickBooks rejected the entry (4xx).",
        last_error_code: "client_error",
      }),
    }),
  ],
  targets: {
    Expense: [{ qbo_id: "2", name: "Office Supplies", account_type: "Expense", classification: "Expense" }],
    Asset: [{ qbo_id: "1", name: "Checking", account_type: "Bank", classification: "Asset" }],
  },
};

const preview = {
  lines: [
    { account_id: "2", posting_type: "Debit", amount: "10.00" },
    { account_id: "1", posting_type: "Credit", amount: "10.00" },
  ],
  debit_total: "10.00",
  credit_total: "10.00",
  balanced: true,
};

function res(status: number, body: unknown) {
  return { ok: status < 400, status, json: async () => body };
}

function stubFetch(overrides: Record<string, (init?: RequestInit) => unknown> = {}) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${url}`;
    if (overrides[key]) return overrides[key](init);
    if (key === "GET /api/review") return res(200, review);
    if (url.endsWith("/preview")) return res(200, preview);
    // any operation endpoint: succeed with a minimal body
    return res(200, { ok: true, sync_status: "posted", posted: 1, failed: 0 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  try {
    window.localStorage.clear();
  } catch {
    // ignore
  }
});

afterEach(() => {
  vi.restoreAllMocks();
});

test("focus queue shows the debit/credit preview and proves it balances", async () => {
  stubFetch();
  render(<FocusQueue />);

  // The transaction in focus (first = categorized).
  await screen.findByRole("heading", { name: "Paper" });

  // Both posting lines, by name, then the balance proof.
  await screen.findByText("Debit");
  expect(screen.getByText("Credit")).toBeInTheDocument();
  expect(screen.getAllByText("Office Supplies").length).toBeGreaterThan(0);
  expect(screen.getByText(/Balanced — debits 10\.00 = credits 10\.00/)).toBeInTheDocument();
});

test("focus queue approves the current entry via the keyboard", async () => {
  const fetchMock = stubFetch();
  render(<FocusQueue />);
  await screen.findByRole("heading", { name: "Paper" });

  fireEvent.keyDown(document.body, { key: "a" });

  await waitFor(() =>
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/journal/transactions/t-cat/approve",
      expect.objectContaining({ method: "POST" }),
    ),
  );
});

test("focus queue navigates to the next transaction with the arrow key", async () => {
  stubFetch();
  render(<FocusQueue />);
  await screen.findByRole("heading", { name: "Paper" });

  fireEvent.keyDown(document.body, { key: "ArrowRight" });
  await screen.findByRole("heading", { name: "Coffee" });
});

test("pipeline board lays out a lane per state with counts", async () => {
  stubFetch();
  render(<PipelineBoard />);

  await screen.findByRole("heading", { name: /Uncategorized/ });
  for (const label of ["Uncategorized", "Categorized", "Approved", "Posted", "Failed"]) {
    expect(screen.getByRole("heading", { name: new RegExp(label) })).toBeInTheDocument();
  }
});

test("pipeline board advances a card with the same approve operation", async () => {
  const fetchMock = stubFetch();
  render(<PipelineBoard />);
  await screen.findByRole("heading", { name: /Categorized/ });

  // The categorized card's per-card advance is Approve.
  fireEvent.click(screen.getByRole("button", { name: "Approve →" }));
  await waitFor(() =>
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/journal/transactions/t-cat/approve",
      expect.objectContaining({ method: "POST" }),
    ),
  );
});

test("pipeline board advances a whole lane (push all) via the real bulk push", async () => {
  const fetchMock = stubFetch();
  render(<PipelineBoard />);
  await screen.findByRole("heading", { name: /Approved/ });

  fireEvent.click(screen.getByRole("button", { name: "Push all" }));
  await waitFor(() =>
    expect(fetchMock).toHaveBeenCalledWith("/api/push", expect.objectContaining({ method: "POST" })),
  );
});

test("the switcher swaps layouts over the same data", async () => {
  stubFetch();
  render(<ReviewWorkspace />);

  // Default: the Ledger table.
  await screen.findByText(/Review the books/i);

  fireEvent.click(screen.getByRole("tab", { name: "Focus" }));
  expect(await screen.findByText(/Focus on one entry/i)).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Paper" })).toBeInTheDocument();

  fireEvent.click(screen.getByRole("tab", { name: "Pipeline" }));
  expect(await screen.findByText(/queue as a pipeline/i)).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: /Uncategorized/ })).toBeInTheDocument();
});

test("the chosen layout is restored from localStorage", async () => {
  window.localStorage.setItem("preferred_review_view", "pipeline");
  stubFetch();
  render(<ReviewWorkspace />);

  // Restored to the pipeline without any interaction.
  expect(await screen.findByText(/queue as a pipeline/i)).toBeInTheDocument();
});

test("choosing a layout persists it to localStorage", async () => {
  stubFetch();
  render(<ReviewWorkspace />);
  await screen.findByText(/Review the books/i);

  fireEvent.click(screen.getByRole("tab", { name: "Focus" }));
  await screen.findByText(/Focus on one entry/i);
  expect(window.localStorage.getItem("preferred_review_view")).toBe("focus");
});
