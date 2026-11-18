import { defineConfig, devices } from "@playwright/test";

/**
 * Browser end-to-end tests against the built app.
 *
 * Uses the system Google Chrome (`channel: "chrome"`); no browser download.
 * Start the built app first, then run:
 *
 *   cd frontend && npm run build
 *   uv run uvicorn backend.main:app --host 127.0.0.1 --port 8788
 *   HTF_E2E=1 npx playwright test
 *
 * `HTF_E2E_BASE_URL` overrides the default loopback URL.
 */
export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.spec.ts",
  timeout: 120_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    ...devices["Desktop Chrome"],
    channel: "chrome",
    baseURL: process.env.HTF_E2E_BASE_URL ?? "http://127.0.0.1:8788",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
  },
});
