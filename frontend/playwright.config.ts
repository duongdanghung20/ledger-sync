import { defineConfig, devices } from "@playwright/test";

// Full-stack e2e: a fake-QuickBooks FastAPI backend (own throwaway Postgres,
// migrated) on :8000, and next dev on :3000 with /api/* proxied to it. No Caddy,
// no real Intuit OAuth. See backend/e2e_server.py and README "End-to-end tests".
export default defineConfig({
  testDir: "./e2e",
  use: { baseURL: "http://localhost:3000" },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        // Escape hatch for hosts where `playwright install` can't fetch this
        // version's browser (e.g. older macOS): point at an installed binary.
        ...(process.env.PW_CHROMIUM_PATH
          ? { launchOptions: { executablePath: process.env.PW_CHROMIUM_PATH } }
          : {}),
      },
    },
  ],
  webServer: [
    {
      command: ".venv/bin/python e2e_server.py",
      cwd: "../backend",
      url: "http://127.0.0.1:8000/api/health",
      timeout: 300_000,
      reuseExistingServer: !process.env.CI,
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      command: "npm run dev",
      url: "http://localhost:3000",
      env: { ...process.env, API_PROXY_TARGET: "http://127.0.0.1:8000" },
      timeout: 120_000,
      reuseExistingServer: !process.env.CI,
    },
  ],
});
