import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { ImportReview } from "../app/import/ImportReview";

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

const banks = [
  { id: "ba-1", name: "Chase Checking", qbo_account_id: "1" },
  { id: "ba-2", name: "Company Card", qbo_account_id: "2" },
];
const profiles = [
  { id: "pr-1", name: "Chase", bank_account_id: "ba-1", config: {} },
  { id: "pr-2", name: "Amex", bank_account_id: "ba-2", config: {} },
];
const catalog = { profiles, bank_accounts: banks };
const preview = { ...catalog, header: ["Posting Date", "Amount"], suggested_profile_id: "pr-1" };
const summary = {
  imported: 4,
  duplicates: 0,
  in_file_duplicate_count: 1,
  rejected: [{ raw: { Description: "" }, reasons: ["empty description"] }],
};

function res(status: number, body: unknown) {
  return { ok: status < 400, status, json: async () => body };
}

function stubFetch(overrides: Record<string, (init?: RequestInit) => unknown> = {}) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${url}`;
    if (overrides[key]) return overrides[key](init);
    if (key === "GET /api/csv-import/profiles") return res(200, catalog);
    if (key === "POST /api/csv-import/preview") return res(200, preview);
    if (key === "POST /api/csv-import") return res(200, summary);
    return res(200, catalog);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function fileInput() {
  return document.querySelector('input[type="file"]') as HTMLInputElement;
}

// jsdom doesn't implement Blob.text() reliably; the component only needs
// file.name + await file.text(), so a stub file stands in for the browser File.
async function chooseFile(text = "Posting Date,Amount\n09/01/2026,-5.00") {
  const file = { name: "chase.csv", text: async () => text };
  fireEvent.change(fileInput(), { target: { files: [file] } });
  return file;
}

afterEach(() => {
  vi.restoreAllMocks();
});

test("previews the file and pre-selects the profile matching its columns", async () => {
  const fetchMock = stubFetch();
  render(<ImportReview />);
  await screen.findByText("Statement file");

  await chooseFile();

  await waitFor(() =>
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/csv-import/preview",
      expect.objectContaining({ method: "POST" }),
    ),
  );
  const mapping = (await screen.findByLabelText("Column mapping")) as HTMLSelectElement;
  expect(mapping.value).toBe("pr-1"); // suggested from the header signature
  // its default bank account is pre-selected too
  expect((screen.getByLabelText("Posts to bank account") as HTMLSelectElement).value).toBe("ba-1");
});

test("imports with the confirmed profile + account and shows the reconciliation", async () => {
  const fetchMock = stubFetch();
  render(<ImportReview />);
  await screen.findByText("Statement file");
  await chooseFile();
  await screen.findByLabelText("Column mapping");

  fireEvent.click(screen.getByRole("button", { name: /import transactions/i }));

  await waitFor(() =>
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/csv-import",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          csv: "Posting Date,Amount\n09/01/2026,-5.00",
          profile_id: "pr-1",
          bank_account_id: "ba-1",
        }),
      }),
    ),
  );

  // the result ledger: imported / possible in-file dups / rejected reasons
  expect(await screen.findByText("Imported")).toBeInTheDocument();
  expect(screen.getByText("Possible duplicates within this file")).toBeInTheDocument();
  expect(screen.getByText("empty description")).toBeInTheDocument();
});

test("changing the mapping re-defaults the bank account to that profile's default", async () => {
  stubFetch();
  render(<ImportReview />);
  await screen.findByText("Statement file");
  await chooseFile();
  await screen.findByLabelText("Column mapping");

  fireEvent.change(screen.getByLabelText("Column mapping"), { target: { value: "pr-2" } });
  expect((screen.getByLabelText("Posts to bank account") as HTMLSelectElement).value).toBe("ba-2");
});

test("with no bank accounts, points at the admin setup and offers no import", async () => {
  stubFetch({
    "GET /api/csv-import/profiles": () => res(200, { profiles: [], bank_accounts: [] }),
  });
  render(<ImportReview />);

  expect(await screen.findByText(/No bank accounts yet/i)).toBeInTheDocument();
  const link = screen.getByRole("link", { name: /admin sets those up/i });
  expect(link).toHaveAttribute("href", "/admin/bank-accounts");
  expect(fileInput()).toBeNull(); // no upload control until an account exists
});

test("shows the sign-in state on 401", async () => {
  stubFetch({ "GET /api/csv-import/profiles": () => res(401, {}) });
  render(<ImportReview />);

  expect(await screen.findByText(/sign in first/i)).toBeInTheDocument();
});
