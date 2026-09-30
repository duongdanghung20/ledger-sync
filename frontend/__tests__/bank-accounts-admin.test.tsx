import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { BankAccountsAdmin } from "../app/admin/bank-accounts/BankAccountsAdmin";

// next/link needs no router context here — render it as a plain anchor.
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

const accounts = [
  { qbo_id: "1", name: "Checking", account_type: "Bank", acct_num: "1000" },
  { qbo_id: "2", name: "Business Card", account_type: "Credit Card", acct_num: "2000" },
];

const payload = {
  bank_accounts: [
    {
      id: "ba-1",
      name: "Operating checking",
      qbo_account_id: "1",
      mapped_account: { qbo_id: "1", name: "Checking", active: true },
    },
  ],
  accounts,
};

function res(status: number, body: unknown) {
  return { ok: status < 400, status, json: async () => body };
}

function stubFetch(overrides: Record<string, (init?: RequestInit) => unknown> = {}) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const key = `${method} ${url}`;
    if (overrides[key]) return overrides[key](init);
    if (url === "/api/bank-accounts" && method === "GET") return res(200, payload);
    return res(200, payload);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.restoreAllMocks();
});

test("lists each bank account with the account it posts to", async () => {
  stubFetch();
  render(<BankAccountsAdmin />);

  expect(await screen.findByText("Operating checking")).toBeInTheDocument();
  // the re-map select shows the current mapping selected
  const select = screen.getByLabelText("Account for Operating checking") as HTMLSelectElement;
  expect(select.value).toBe("1");
  // the option appears in both the create picker and the row's re-map picker
  expect(screen.getAllByRole("option", { name: "Business Card · 2000" }).length).toBeGreaterThan(0);
});

test("adding posts the name + mapped account and re-renders", async () => {
  const created = {
    bank_accounts: [
      ...payload.bank_accounts,
      {
        id: "ba-2",
        name: "Company card",
        qbo_account_id: "2",
        mapped_account: { qbo_id: "2", name: "Business Card", active: true },
      },
    ],
    accounts,
  };
  const fetchMock = stubFetch({ "POST /api/bank-accounts": () => res(201, created) });
  render(<BankAccountsAdmin />);
  await screen.findByText("Operating checking");

  fireEvent.change(screen.getByLabelText("Bank account"), { target: { value: "Company card" } });
  fireEvent.change(screen.getByLabelText("Posts to account"), { target: { value: "2" } });
  fireEvent.click(screen.getByRole("button", { name: /^add$/i }));

  await waitFor(() =>
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/bank-accounts",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ name: "Company card", qbo_account_id: "2" }),
      }),
    ),
  );
  expect(await screen.findByText("Company card")).toBeInTheDocument();
});

test("re-mapping PATCHes the new qbo_account_id", async () => {
  const fetchMock = stubFetch({
    "PATCH /api/bank-accounts/ba-1": () =>
      res(200, {
        bank_accounts: [
          { ...payload.bank_accounts[0], qbo_account_id: "2", mapped_account: { qbo_id: "2", name: "Business Card", active: true } },
        ],
        accounts,
      }),
  });
  render(<BankAccountsAdmin />);
  await screen.findByText("Operating checking");

  fireEvent.change(screen.getByLabelText("Account for Operating checking"), {
    target: { value: "2" },
  });

  await waitFor(() =>
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/bank-accounts/ba-1",
      expect.objectContaining({ method: "PATCH", body: JSON.stringify({ qbo_account_id: "2" }) }),
    ),
  );
});

test("flags a mapping whose account is no longer active", async () => {
  stubFetch({
    "GET /api/bank-accounts": () =>
      res(200, {
        bank_accounts: [
          {
            id: "ba-1",
            name: "Old checking",
            qbo_account_id: "9",
            mapped_account: { qbo_id: "9", name: "Closed Savings", active: false },
          },
        ],
        accounts,
      }),
  });
  render(<BankAccountsAdmin />);

  expect(await screen.findByText(/no longer active in QuickBooks/i)).toBeInTheDocument();
});

test("shows the admin-only state on 403", async () => {
  stubFetch({ "GET /api/bank-accounts": () => res(403, {}) });
  render(<BankAccountsAdmin />);

  expect(await screen.findByText(/admin only/i)).toBeInTheDocument();
});
