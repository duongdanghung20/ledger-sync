import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { RulesManager } from "../app/rules/RulesManager";

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

const targets = {
  Expense: [
    { qbo_id: "exp-meals", name: "Meals & Entertainment" },
    { qbo_id: "exp-office", name: "Office Supplies" },
  ],
};
const rules = [
  {
    id: "r1",
    priority: 10,
    target_qbo_account_id: "exp-meals",
    conditions: [{ field: "payee", operator: "contains", value: "starbucks" }],
    invalid_target: false,
  },
  {
    id: "r2",
    priority: 20,
    target_qbo_account_id: "exp-office",
    conditions: [
      { field: "description", operator: "contains", value: "amzn" },
      { field: "amount", operator: "lte", value: "-100" },
    ],
    invalid_target: true,
  },
];
const catalog = { rules, targets };
const summary = { total: 8, by_rule: 5, manual: 1, uncategorized: 2 };

function res(status: number, body: unknown) {
  return { ok: status < 400, status, json: async () => body };
}

function stubFetch(overrides: Record<string, (init?: RequestInit) => unknown> = {}) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${url}`;
    if (overrides[key]) return overrides[key](init);
    if (key === "GET /api/categorization/rules") return res(200, catalog);
    if (key === "POST /api/categorization/rules") return res(201, catalog);
    if (key === "POST /api/categorization/run") return res(200, summary);
    return res(200, catalog);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.restoreAllMocks();
});

test("renders each rule as a clause in evaluation order with its account", async () => {
  stubFetch();
  render(<RulesManager />);

  const list = await screen.findByRole("list");
  const items = within(list).getAllByRole("listitem");
  expect(items).toHaveLength(2);
  expect(items[0]).toHaveTextContent("payee");
  expect(items[0]).toHaveTextContent("Meals & Entertainment");
  // the AND rule reads both conditions
  expect(items[1]).toHaveTextContent("amzn");
  expect(items[1]).toHaveTextContent("and");
});

test("flags a rule whose target account has gone inactive", async () => {
  stubFetch();
  render(<RulesManager />);
  await screen.findByRole("list");
  expect(screen.getByText(/inactive in QuickBooks/i)).toBeInTheDocument();
});

test("adds a rule and posts the conditions and target", async () => {
  const fetchMock = stubFetch();
  render(<RulesManager />);
  await screen.findByRole("list");

  fireEvent.click(screen.getByRole("button", { name: /add a rule/i }));
  fireEvent.change(screen.getByLabelText("Condition 1 value"), { target: { value: "shell" } });
  fireEvent.change(screen.getByLabelText("Post to account"), { target: { value: "exp-office" } });
  fireEvent.click(screen.getByRole("button", { name: /^add rule$/i }));

  await waitFor(() =>
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/categorization/rules",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          priority: 30,
          target_qbo_account_id: "exp-office",
          conditions: [{ field: "payee", operator: "contains", value: "shell" }],
        }),
      }),
    ),
  );
});

test("switching a condition to amount offers numeric operators and a between upper bound", async () => {
  stubFetch();
  render(<RulesManager />);
  await screen.findByRole("list");
  fireEvent.click(screen.getByRole("button", { name: /add a rule/i }));

  fireEvent.change(screen.getByLabelText("Condition 1 field"), { target: { value: "amount" } });
  const op = screen.getByLabelText("Condition 1 operator") as HTMLSelectElement;
  expect(within(op).queryByText("contains")).toBeNull();
  fireEvent.change(op, { target: { value: "between" } });
  expect(screen.getByLabelText("Condition 1 upper value")).toBeInTheDocument();
});

test("surfaces a validation error from the server without closing the form", async () => {
  stubFetch({
    "POST /api/categorization/rules": () => res(422, { detail: "A rule needs at least one condition." }),
  });
  render(<RulesManager />);
  await screen.findByRole("list");
  fireEvent.click(screen.getByRole("button", { name: /add a rule/i }));
  fireEvent.click(screen.getByRole("button", { name: /^add rule$/i }));

  expect(await screen.findByText("A rule needs at least one condition.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /^add rule$/i })).toBeInTheDocument(); // form stays open
});

test("running categorization shows the reconciliation summary", async () => {
  stubFetch();
  render(<RulesManager />);
  await screen.findByRole("list");

  fireEvent.click(screen.getByRole("button", { name: /run categorization/i }));

  expect(await screen.findByText("Matched by a rule")).toBeInTheDocument();
  expect(screen.getByText("Waiting to be categorized")).toBeInTheDocument();
});

test("points at QuickBooks when there are no accounts to post to", async () => {
  stubFetch({
    "GET /api/categorization/rules": () => res(200, { rules: [], targets: {} }),
  });
  render(<RulesManager />);
  expect(await screen.findByText(/No accounts to post to yet/i)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /connect quickbooks/i })).toHaveAttribute(
    "href",
    "/settings/quickbooks",
  );
});

test("shows the sign-in state on 401", async () => {
  stubFetch({ "GET /api/categorization/rules": () => res(401, {}) });
  render(<RulesManager />);
  expect(await screen.findByText(/sign in first/i)).toBeInTheDocument();
});
