import { expect, test } from "@playwright/test";

test("landing page shows the Ledger-Sync wordmark", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Ledger-Sync" })).toBeVisible();
});
