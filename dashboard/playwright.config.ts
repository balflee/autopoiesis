import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config — Dashboard smoke harness.
 *
 * Boots `next start` (production build) before tests run, then targets
 * Chromium at mobile + desktop viewports. The mobile screenshot is the
 * one the demo team cares about (PRD §8 — VitalsPanel + DeathWatch
 * must render legibly at 375 px).
 *
 * Tests inject mock WS state via `window.__GENESIS_MOCK_WS__` — see
 * __tests__/playwright/dashboard_smoke.spec.ts — so no real agent
 * backend is required.
 */
export default defineConfig({
  // Two roots so the brief-canonical `tests/e2e/` location is honoured AND
  // the legacy `__tests__/playwright/` specs keep running. T-D-008 introduced
  // the second root — see dashboard/tests/e2e/backtest.spec.ts.
  testDir: ".",
  testMatch: [
    "__tests__/playwright/**/*.spec.ts",
    "tests/e2e/**/*.spec.ts",
    // T-D-009 sprint_8 — sandbox-live spec at the brief-canonical path.
    "tests/dashboard/playwright/**/*.spec.ts",
    // T-D-010 sprint_9 — workshop + agent controls + reflection feed +
    // proposal review specs at the brief-canonical path.
    "tests/dashboard/e2e/**/*.spec.ts",
    // T-D-011 sprint_10 — server-side proxy specs.
    "tests/dashboard/proxy/**/*.spec.ts",
  ],
  fullyParallel: false,
  retries: 0,
  timeout: 30_000,
  expect: { timeout: 5_000 },
  reporter: [
    ["list"],
    ["html", { open: "never", outputFolder: "screenshots/playwright-report" }],
  ],
  use: {
    baseURL: "http://127.0.0.1:3100",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "desktop",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 800 } },
    },
    {
      name: "mobile",
      use: { ...devices["Pixel 5"], viewport: { width: 375, height: 700 } },
    },
  ],
  // Two webServers: the Next.js production build under test AND a tiny
  // mock upstream HTTP server that stands in for the Track B FastAPI for
  // the T-D-011 proxy specs. The proxy specs forward through the real
  // Next.js route to this mock — anything else (e.g. workshop_flow specs)
  // either mocks via `page.route` BEFORE the request reaches Next.js, or
  // doesn't talk to the proxy at all. So adding the mock is a safe no-op
  // for unrelated suites.
  webServer: [
    {
      command: "npm run start -- -p 3100",
      url: "http://127.0.0.1:3100",
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
      stdout: "ignore",
      stderr: "pipe",
      env: {
        // T-D-011 — server-only env vars that the proxy route reads at
        // request time. Setting them here makes the proxy specs hermetic
        // against the host environment.
        DASHBOARD_API_URL: "http://127.0.0.1:8765",
        DASHBOARD_API_TOKEN: "test-token-genesis-T-D-011",
        // Enables the proxy's `x-genesis-proxy-test-clear-token` test
        // seam. Production deploys leave this unset so the seam is
        // permanently disabled outside the test harness.
        PROXY_TEST_MODE: "1",
      },
    },
    {
      command: "node tests/dashboard/proxy/mock_upstream.mjs",
      url: "http://127.0.0.1:8765/healthz",
      reuseExistingServer: !process.env.CI,
      timeout: 10_000,
      stdout: "ignore",
      stderr: "pipe",
      env: {
        PROXY_MOCK_PORT: "8765",
        // Sanity gate — mock upstream returns 401 if the proxy forwards
        // a token that doesn't match this exact value.
        MOCK_UPSTREAM_EXPECTED_TOKEN: "test-token-genesis-T-D-011",
      },
    },
  ],
});
