import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { LedgerReview } from "../app/LedgerReview";

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

const review = {
  transactions: [
    row("t-un", "Coffee", "uncategorized", { assigned_account_qbo_id: null, category_source: "none" }),
    row("t-cat", "Paper", "categorized", { assigned_account_qbo_id: "2", payee: "Staples" }),
    row("t-app", "Rent", "approved", {
      assigned_account_qbo_id: "2",
      journal_entry: je({ id: "je-app", sync_status: "pending", doc_number: 1 }),
    }),
    row("t-post", "Utilities", "posted", {
      assigned_account_qbo_id: "2",
      journal_entry: je({ id: "je-post", sync_status: "posted", doc_number: 2, attempt_count: 1 }),
    }),
    row("t-fail", "Software", "failed", {
      assigned_account_qbo_id: "2",
      journal_entry: je({
        id: "je-fail",
        sync_status: "failed",
        doc_number: 3,
        last_error: "QuickBooks rejected the entry (4xx).",
        last_error_code: "client_error",
        attempt_count: 2,
      }),
    }),
  ],
  targets: {
    Expense: [{ qbo_id: "2", name: "Office Supplies", account_type: "Expense", classification: "Expense" }],
    Asset: [{ qbo_id: "1", name: "Checking", account_type: "Bank", classification: "Asset" }],
  },
};

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

function res(status: number, body: unknown) {
  return { ok: status < 400, status, json: async () => body };
}

function stubFetch(overrides: Record<string, (init?: RequestInit) => unknown> = {}) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${url}`;
    if (overrides[key]) return overrides[key](init);
    if (key === "GET /api/review") return res(200, review);
    // any operation endpoint: succeed with a minimal body
    return res(200, { ok: true });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.restoreAllMocks();
});

test("renders a tab per state with counts and filters the sheet", async () => {
  stubFetch();
  render(<LedgerReview />);
  await screen.findByRole("tab", { name: /All/ });

  expect(screen.getByRole("tab", { name: /Uncategorized\s*1/ })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: /Failed\s*1/ })).toBeInTheDocument();
  // all five rows visible under "All"
  expect(screen.getByText("Coffee")).toBeInTheDocument();
  expect(screen.getByText("Rent")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("tab", { name: /Uncategorized/ }));
  expect(screen.getByText("Coffee")).toBeInTheDocument();
  expect(screen.queryByText("Rent")).not.toBeInTheDocument(); // filtered out
});

test("approval is barred for an uncategorized row, enabled once categorized", async () => {
  stubFetch();
  render(<LedgerReview />);
  await screen.findByRole("tab", { name: /All/ });

  // the uncategorized row's Approve is disabled with an explanation
  const barred = screen.getByRole("button", { name: /assign an account first/i });
  expect(barred).toBeDisabled();

  // the categorized row's Approve is enabled
  const approve = screen.getByRole("button", { name: "Approve" });
  expect(approve).toBeEnabled();
});

test("assigning an account calls the assign endpoint", async () => {
  const fetchMock = stubFetch();
  render(<LedgerReview />);
  await screen.findByRole("tab", { name: /All/ });

  fireEvent.change(screen.getByLabelText("Account for Coffee"), { target: { value: "2" } });

  await waitFor(() =>
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/categorization/transactions/t-un/assign",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ qbo_account_id: "2" }),
      }),
    ),
  );
});

test("bulk approve gates on selection and approves the selected categorized rows", async () => {
  const fetchMock = stubFetch();
  render(<LedgerReview />);
  await screen.findByRole("tab", { name: /All/ });

  const bulk = screen.getByRole("button", { name: /Approve selected/i });
  expect(bulk).toBeDisabled(); // nothing selected yet

  fireEvent.click(screen.getByLabelText("Select Paper"));
  expect(screen.getByRole("button", { name: /Approve selected \(1\)/ })).toBeEnabled();

  fireEvent.click(screen.getByRole("button", { name: /Approve selected/i }));
  await waitFor(() =>
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/journal/transactions/t-cat/approve",
      expect.objectContaining({ method: "POST" }),
    ),
  );
});

test("a failed row surfaces its error and retry re-pushes that entry", async () => {
  const fetchMock = stubFetch();
  render(<LedgerReview />);
  await screen.findByRole("tab", { name: /All/ });

  expect(screen.getByText("QuickBooks rejected the entry (4xx).")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await waitFor(() =>
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/push/entries/je-fail/retry",
      expect.objectContaining({ method: "POST" }),
    ),
  );
});

test("approved row offers un-approve; posted row is read-only", async () => {
  stubFetch();
  render(<LedgerReview />);
  await screen.findByRole("tab", { name: /All/ });

  expect(screen.getByRole("button", { name: /Un-approve/i })).toBeInTheDocument();
  expect(screen.getByText(/Posted · #2/)).toBeInTheDocument();
  // posted row is not selectable (no checkbox) and has no account picker
  expect(screen.queryByLabelText("Select Utilities")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Account for Utilities")).not.toBeInTheDocument();
});

test("wires nav entry points for the orphaned feature screens", async () => {
  stubFetch();
  render(<LedgerReview />);
  await screen.findByRole("tab", { name: /All/ });

  const hrefs = screen.getAllByRole("link").map((a) => a.getAttribute("href"));
  for (const href of ["/import", "/rules", "/admin/bank-accounts", "/admin/users", "/settings/quickbooks"]) {
    expect(hrefs).toContain(href);
  }
});

test("shows the sign-in state on 401", async () => {
  stubFetch({ "GET /api/review": () => res(401, {}) });
  render(<LedgerReview />);
  expect(await screen.findByText(/sign in first/i)).toBeInTheDocument();
});
