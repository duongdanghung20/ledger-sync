import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["__tests__/**/*.test.{ts,tsx}"],
    // Playwright owns e2e/; keep it out of the unit runner.
    exclude: ["node_modules/**", "e2e/**", ".next/**"],
  },
});
