import { defineConfig, devices } from "@playwright/test";

// e2e: the production standalone build (as in Docker) against a fake console API that serves JSON fixtures
// recorded from the real console API over the persona-seeded fake bank (tests/e2e/fixtures). No network.
// Ports are overridable so several checkouts (e.g. git worktrees) can run e2e at the same time.
const FAKE_API_PORT = Number(process.env.E2E_API_PORT ?? 8599);
const APP_PORT = Number(process.env.E2E_APP_PORT ?? 8598);

export default defineConfig({
  testDir: "tests/e2e",
  fullyParallel: false,
  workers: 1, // one fake API whose up/down switch is shared
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: { baseURL: `http://127.0.0.1:${APP_PORT}`, trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: `node tests/e2e/fake-console-api.mjs ${FAKE_API_PORT}`,
      url: `http://127.0.0.1:${FAKE_API_PORT}/healthz`,
      reuseExistingServer: !process.env.CI,
    },
    {
      command: `node scripts/start-standalone.mjs ${APP_PORT} 127.0.0.1`,
      url: `http://127.0.0.1:${APP_PORT}/healthz`,
      reuseExistingServer: !process.env.CI,
      env: {
        CONSOLE_API_URL: `http://127.0.0.1:${FAKE_API_PORT}`,
        CONSOLE_API_TOKEN: "e2e-token",
      },
    },
  ],
});
