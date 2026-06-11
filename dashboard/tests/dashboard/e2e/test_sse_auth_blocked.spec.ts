import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * SSE auth-blocked banner — T-D-010 Round-2 fix.
 *
 * Prior review (round 1) flagged that the dashboard's SSE client smuggled
 * the bearer token into the URL as `?token=<jwt>` and FALSELY documented
 * that `agent/server/auth.py` accepted it. The backend `require_bearer_token`
 * dependency reads ONLY the `Authorization` header, so every authenticated
 * SSE handshake from the browser collapsed to 401 silently.
 *
 * The round-2 fix:
 *
 *   1. Drop the URL-token entirely (closes the URL-leakage anti-pattern).
 *   2. Detect "EventSource cannot open + a token IS configured client-side"
 *      and surface a synthetic `auth_blocked` status the consumers render
 *      as an honest banner explaining the gap + pointing to sprint_10.
 *
 * This spec proves the banner appears across all three SSE consumers
 * (AgentControls / ReflectionFeed / ProposalReview) when the EventSource
 * cannot open AND a token is configured.
 */

const SCREENSHOT_DIR = "screenshots/T-D-010";

test.beforeAll(() => {
  fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
});

const STATUS_RUNNING = {
  phase: "PHASE_2_APPRENTICE",
  breath: 72.3,
  last_tick_ts: "2026-05-27T18:00:00Z",
  current_weights: null,
  llm_cost_usd_this_month: 0.0,
  pending_proposals_count: 0,
  running: true,
  run_id: "run_sse_auth_block",
};

async function gotoLiveDashboardWithToken(
  page: import("@playwright/test").Page,
): Promise<void> {
  // Seed the localStorage token BEFORE the first navigation so the SSE
  // client sees it on mount. This is the operator-configured-token
  // scenario the banner targets.
  await page.addInitScript(() => {
    try {
      window.localStorage.setItem(
        "genesis_api_token",
        "fake-token-for-banner-trigger",
      );
    } catch {
      /* ignore */
    }
  });

  await page.goto("/");
  await expect(page.getByTestId("playback-takeover")).toBeVisible();
  await page.keyboard.press("Escape");
}

test.describe("SSE auth-blocked banner — when token is set but stream cannot open", () => {
  test("ReflectionFeed surfaces the sprint_10 banner when SSE is rejected", async ({ page }, testInfo) => {
    // Backend returns 401 on /api/state/stream — simulates the real
    // require_bearer_token dependency rejecting the unauthenticated
    // EventSource handshake.
    await page.route("**/api/state/stream**", async (route) => {
      await route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ detail: "unauthorized" }),
      });
    });
    await page.route("**/api/agent/status", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(STATUS_RUNNING),
      });
    });

    await gotoLiveDashboardWithToken(page);

    const feed = page.getByTestId("reflection-feed");
    await expect(feed).toBeVisible();
    // The banner appears once the SSE error handler runs + we detect the
    // token-was-present + never-opened condition. Generous timeout because
    // EventSource's internal reconnect happens before the CLOSED state.
    const banner = page.getByTestId("reflection-feed-auth-banner");
    await expect(banner).toBeVisible({ timeout: 15_000 });
    await expect(banner).toContainText(/sprint_10/i);
    await expect(banner).toContainText(/EventSource cannot send/i);

    const suffix = testInfo.project.name === "mobile" ? "mobile" : "desktop";
    await page.screenshot({
      path: path.join(
        SCREENSHOT_DIR,
        `09-reflection-feed-auth-blocked-${suffix}.png`,
      ),
      fullPage: false,
      clip: { x: 0, y: 0, width: 1280, height: 720 },
    });
  });

  test("AgentControls surfaces the sprint_10 banner when SSE is rejected", async ({ page }) => {
    await page.route("**/api/state/stream**", async (route) => {
      await route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ detail: "unauthorized" }),
      });
    });
    await page.route("**/api/agent/status", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(STATUS_RUNNING),
      });
    });

    await gotoLiveDashboardWithToken(page);

    const controls = page.getByTestId("agent-controls");
    await expect(controls).toBeVisible();
    const banner = page.getByTestId("agent-controls-sse-banner");
    await expect(banner).toBeVisible({ timeout: 15_000 });
    await expect(banner).toContainText(/sprint_10/i);
    // Pill still reports running because /api/agent/status polls succeed.
    await expect(controls).toHaveAttribute("data-status", "running");
  });

  test("ProposalReview surfaces the sprint_10 banner when SSE is rejected", async ({ page }) => {
    await page.route("**/api/state/stream**", async (route) => {
      await route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ detail: "unauthorized" }),
      });
    });
    await page.route("**/api/agent/status", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(STATUS_RUNNING),
      });
    });

    await gotoLiveDashboardWithToken(page);

    const review = page.getByTestId("proposal-review");
    await expect(review).toBeVisible();
    const banner = page.getByTestId("proposal-review-auth-banner");
    await expect(banner).toBeVisible({ timeout: 15_000 });
    // T-D-012 (sprint_10) — the banner copy now explains the proxy gap
    // (the sprint_10 wiring landed, but if the server-side token is
    // missing the proxy still 401s the EventSource handshake).
    await expect(banner).toContainText(/live stream unavailable/i);
  });

  test("SSE URL never contains a token query string (regression test for round 1)", async ({ page }) => {
    let sseUrlCapture: string | null = null;
    await page.route("**/api/state/stream**", async (route) => {
      sseUrlCapture = route.request().url();
      await route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ detail: "unauthorized" }),
      });
    });
    await page.route("**/api/agent/status", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(STATUS_RUNNING),
      });
    });

    await gotoLiveDashboardWithToken(page);
    // Wait for SSE to be requested.
    await expect.poll(() => sseUrlCapture, { timeout: 10_000 }).not.toBeNull();
    expect(sseUrlCapture).toBeTruthy();
    // The URL must NOT smuggle the bearer token as a query-string param.
    expect(sseUrlCapture).not.toMatch(/[?&]token=/);
  });
});
