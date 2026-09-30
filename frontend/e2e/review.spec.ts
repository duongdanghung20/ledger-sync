import { expect, test } from "@playwright/test";

// The money-path happy path over the running stack with fake QuickBooks:
// import -> categorize -> approve -> push. The backend seeds a connected Book,
// a mirror, a bank account, a default column-mapping profile and this Bookkeeper
// (QBO_FAKE=1); the test drives the four operations through the UI.

const EMAIL = "bookkeeper@example.com";
const PASSWORD = "ledger-sync-e2e";

test("import → categorize → approve → push posts the entry to QuickBooks", async ({ page }) => {
  // Unique description per run so re-running against a persistent DB never dedups.
  const desc = `Coffee ${Date.now()}`;
  const csv = `Date,Description,Amount\n2026-09-01,${desc},-4.50\n`;

  // 1. Sign in as the seeded Bookkeeper.
  await page.goto("/login");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Ledger-Sync" })).toBeVisible();

  // 2. Import a one-row bank statement (seeded profile + bank account).
  await page.goto("/import");
  await expect(page.getByText("Statement file")).toBeVisible();
  await page.setInputFiles('input[type="file"]', {
    name: "statement.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(csv),
  });
  await expect(page.getByLabel("Column mapping")).toBeVisible();
  await page.getByRole("button", { name: /Import transactions/i }).click();
  await expect(page.getByText("Imported")).toBeVisible();

  // 3. Review the imported transaction (uncategorized) and assign an account.
  //    Scope every step to this run's row so leftover rows never mislead.
  await page.goto("/");
  const row = page.getByRole("row").filter({ hasText: desc });
  await expect(row).toBeVisible();
  await row.getByLabel(`Account for ${desc}`).selectOption({ label: "Office Supplies" });

  // 4. Approve → a pending Journal Entry. Auto-waits for the row to become
  //    categorized (its Approve enables); exact avoids matching "Approve selected".
  await row.getByRole("button", { name: "Approve", exact: true }).click();
  await expect(row.getByText(/Approved/)).toBeVisible();

  // 5. Push the approved entry → posted to (fake) QuickBooks.
  await row.getByLabel(`Select ${desc}`).check();
  await page.getByRole("button", { name: /Push selected/ }).click();
  await expect(row.getByText(/Posted/)).toBeVisible();
});
