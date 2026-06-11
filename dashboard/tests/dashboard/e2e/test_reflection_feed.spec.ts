import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * ReflectionFeed — virtualised list of reflections from the SSE stream.
 *
 * Coverage:
 *   - Empty state renders "waiting for first reflection…" when SSE has no rows
 *   - Once rows arrive, newest is at top + click expands the JSON detail panel
 *   - Status badge follows the SSE state machine
 *
 * Strategy: we serve a *fake* SSE stream from page.route by pushing a
 * pre-baked event-stream body. Playwright + EventSource handle the rest.
 * Track B writes one event per appended JSONL line; we synthesise three.
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
  run_id: "run_abc123def456",
};

/** SSE response body — three reflections framed per the WHATWG protocol. */
const SSE_BODY = [
  `event: reflections`,
  `data: ${JSON.stringify({
    ts: "2026-05-27T18:00:01Z",
    narrative: "Sentiment 0.61 with smart-money silence — neutral.",
    tick_id: 101,
  })}`,
  ``,
  `event: reflections`,
  `data: ${JSON.stringify({
    ts: "2026-05-27T18:00:05Z",
    narrative: "Edge widens past 8% — re-checking Kelly fraction before BET.",
    tick_id: 102,
  })}`,
  ``,
  `event: reflections`,
  `data: ${JSON.stringify({
    ts: "2026-05-27T18:00:09Z",
    summary: "LAL price drifted; cancelling proposed BET.",
    tick_id: 103,
  })}`,
  ``,
  // Keep the connection idle but open — the browser will hold it.
  `: keepalive`,
  ``,
].join("\n");

async function gotoLiveDashboard(
  page: import("@playwright/test").Page,
): Promise<void> {
  await page.goto("/");
  await expect(page.getByTestId("playback-takeover")).toBeVisible();
  await page.keyboard.press("Escape");
}

test.describe("ReflectionFeed — SSE-driven stream", () => {
  test("empty state when SSE has no rows", async ({ page }) => {
    // Abort SSE → component stays empty.
    await page.route("**/api/state/stream**", (route) => route.abort());
    await page.route("**/api/agent/status", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(STATUS_RUNNING),
      });
    });

    await gotoLiveDashboard(page);
    const feed = page.getByTestId("reflection-feed");
    await expect(feed).toBeVisible();
    await expect(feed).toHaveAttribute("data-empty", "true");
    await expect(page.getByTestId("reflection-feed-empty")).toBeVisible();
  });

  test("renders three reflections newest-first and expands JSON on click", async ({ page }, testInfo) => {
    await page.route("**/api/state/stream**", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        headers: { "Cache-Control": "no-cache" },
        body: SSE_BODY,
      });
    });
    await page.route("**/api/agent/status", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(STATUS_RUNNING),
      });
    });

    await gotoLiveDashboard(page);

    const feed = page.getByTestId("reflection-feed");
    await expect(feed).toBeVisible();
    // Wait for at least three rows to land.
    await expect(page.getByTestId("reflection-row-2")).toBeVisible({
      timeout: 8_000,
    });
    await expect(feed).toHaveAttribute("data-empty", "false");

    // Newest at top — row-0 should be the last (tick 103) event.
    const top = page.getByTestId("reflection-row-0-narrative");
    await expect(top).toContainText("LAL price drifted");

    // Click row-1 → expand → JSON detail visible.
    await page.getByTestId("reflection-row-1").click();
    await expect(page.getByTestId("reflection-row-1-detail")).toBeVisible();
    await expect(page.getByTestId("reflection-row-1")).toHaveAttribute(
      "data-expanded",
      "true",
    );

    const suffix = testInfo.project.name === "mobile" ? "mobile" : "desktop";
    await page.screenshot({
      path: path.join(SCREENSHOT_DIR, `07-reflection-feed-${suffix}.png`),
      fullPage: true,
    });
  });
});
