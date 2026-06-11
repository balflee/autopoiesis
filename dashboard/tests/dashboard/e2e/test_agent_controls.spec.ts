import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * AgentControls — header bar on the `/` route. T-D-010 sprint_9.
 *
 * Coverage:
 *   - status pill renders the live status ("running" / "stopped" / "offline")
 *   - start button POSTs /api/agent/start; on 409 the inline note surfaces
 *   - stop button POSTs /api/agent/stop; idempotent
 *   - BREATH ticker visible + reads the /status snapshot value
 *   - offline state surfaces a retry button + does NOT crash the route
 *
 * The component auto-fetches /api/agent/status on mount and subscribes to
 * /api/state/stream. We mock the JSON endpoint via `page.route`; the SSE
 * endpoint is left to fail (EventSource auto-retries, AgentControls
 * surfaces this as a state change but never crashes).
 */

const SCREENSHOT_DIR = "screenshots/T-D-010";

test.beforeAll(() => {
  fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
});

const STATUS_RUNNING = {
  phase: "PHASE_2_APPRENTICE",
  breath: 72.3,
  last_tick_ts: "2026-05-27T18:00:00Z",
  current_weights: { w_r: 0.5, w_s: 0.5, alpha: 0.5, beta: 0.5, rho: 0.0 },
  llm_cost_usd_this_month: 0.42,
  pending_proposals_count: 0,
  running: true,
  run_id: "run_8f4ad21c0e9b",
};

const STATUS_STOPPED = { ...STATUS_RUNNING, running: false };

async function gotoLiveDashboard(
  page: import("@playwright/test").Page,
): Promise<void> {
  await page.goto("/");
  // PLAYBACK auto-takeover — press Escape to drop into LIVE.
  await expect(page.getByTestId("playback-takeover")).toBeVisible();
  await page.keyboard.press("Escape");
}

test.describe("AgentControls — header bar", () => {
  test("renders running pill + BREATH value from /api/agent/status", async ({ page }) => {
    await page.route("**/api/agent/status", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(STATUS_RUNNING),
      });
    });
    // SSE endpoint — fail fast so AgentControls just polls.
    await page.route("**/api/state/stream**", (route) => route.abort());

    await gotoLiveDashboard(page);

    const controls = page.getByTestId("agent-controls");
    await expect(controls).toBeVisible();
    await expect(controls).toHaveAttribute("data-status", /(running|stopped)/);

    // Wait for the snapshot fetch to settle into "running".
    await expect(controls).toHaveAttribute("data-status", "running", {
      timeout: 5_000,
    });

    await expect(page.getByTestId("agent-status-pill")).toContainText("running");
    await expect(page.getByTestId("breath-ticker")).toBeVisible();
    await expect(page.getByTestId("breath-ticker-value")).toContainText("72.3");
    await expect(page.getByTestId("agent-controls-phase")).toContainText(
      /apprentice/i,
    );
    await expect(page.getByTestId("agent-controls-run-id")).toContainText(
      "run_8f4a",
    );

    await page.screenshot({
      path: path.join(SCREENSHOT_DIR, "05-agent-controls-running.png"),
      fullPage: false,
      clip: { x: 0, y: 0, width: 1280, height: 240 },
    });
  });

  test("start button POSTs /api/agent/start; 409 surfaces 'already running'", async ({ page }) => {
    let startCalls = 0;
    await page.route("**/api/agent/status", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(STATUS_STOPPED),
      });
    });
    await page.route("**/api/agent/start", async (route) => {
      startCalls += 1;
      if (startCalls === 1) {
        await route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({
            detail: "agent already running",
            run_id: "run_xyz_already",
          }),
        });
      } else {
        await route.fulfill({
          status: 202,
          contentType: "application/json",
          body: JSON.stringify({ run_id: "run_new", status: "accepted" }),
        });
      }
    });
    await page.route("**/api/state/stream**", (route) => route.abort());

    await gotoLiveDashboard(page);
    await expect(page.getByTestId("agent-controls")).toBeVisible();

    await page.getByTestId("agent-start-button").click();
    await expect(page.getByTestId("agent-controls-note")).toContainText(
      /already running/i,
    );
    expect(startCalls).toBe(1);
  });

  test("stop button POSTs /api/agent/stop and updates the pill", async ({ page }) => {
    let statusCalls = 0;
    await page.route("**/api/agent/status", async (route) => {
      statusCalls += 1;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(statusCalls <= 1 ? STATUS_RUNNING : STATUS_STOPPED),
      });
    });
    await page.route("**/api/agent/stop", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          status: "stopped",
          final_state_path: "/tmp/agent_state.json",
        }),
      });
    });
    await page.route("**/api/state/stream**", (route) => route.abort());

    await gotoLiveDashboard(page);
    await expect(page.getByTestId("agent-controls")).toHaveAttribute(
      "data-status",
      "running",
      { timeout: 5_000 },
    );

    await page.getByTestId("agent-stop-button").click();
    await expect(page.getByTestId("agent-controls")).toHaveAttribute(
      "data-status",
      "stopped",
      { timeout: 5_000 },
    );
    await expect(page.getByTestId("agent-controls-note")).toContainText(
      /stopped/i,
    );
  });

  test("offline state surfaces a retry button and the dashboard does not crash", async ({ page }) => {
    // Abort EVERY backend call → AgentControls must go offline gracefully.
    await page.route("**/api/agent/**", (route) => route.abort());
    await page.route("**/api/state/stream**", (route) => route.abort());

    await gotoLiveDashboard(page);

    const controls = page.getByTestId("agent-controls");
    await expect(controls).toBeVisible({ timeout: 10_000 });
    await expect(controls).toHaveAttribute("data-status", "offline", {
      timeout: 10_000,
    });
    await expect(page.getByTestId("agent-retry-button")).toBeVisible();

    // The rest of the dashboard is still functional — VitalsPanel et al.
    // are still in DOM (data-loading=true is fine here).
    await expect(page.getByTestId("vitals-panel")).toBeVisible();

    await page.screenshot({
      path: path.join(SCREENSHOT_DIR, "06-agent-controls-offline.png"),
      fullPage: false,
      clip: { x: 0, y: 0, width: 1280, height: 240 },
    });
  });
});
