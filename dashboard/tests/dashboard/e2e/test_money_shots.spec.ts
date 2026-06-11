import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * Money-Shot screenshots for T-D-010 sprint_9 visual sign-off.
 *
 * needs_human_review=true on this task — the user signs off on the visual
 * before merge. The remaining scenes all target the LIVE dashboard at `/`:
 *
 *   2. AgentControls running
 *   3. ReflectionFeed >= 3 entries
 *   4. ProposalReview empty state
 *   5. Full dashboard composite
 *
 * G2: "MONEY SHOT 01 — workshop with completed sweep" was DROPPED — the
 * /workshop sweep surface was folded into /roadmap (it now 307-redirects),
 * so there is no workshop sweep table left to screenshot.
 *
 * The Money-Shots live at:
 *   screenshots/T-D-010/money_shot_02_agent_controls_running.png
 *   screenshots/T-D-010/money_shot_03_reflection_feed_{desktop,mobile}.png
 *   screenshots/T-D-010/money_shot_04_proposal_review_empty_{desktop,mobile}.png
 *   screenshots/T-D-010/money_shot_05_full_dashboard_{desktop,mobile}.png
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
  run_id: "run_a1b2c3d4e5f6",
};

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

// G2: "MONEY SHOT 01 — workshop with completed sweep" was removed; the
// /workshop sweep surface was folded into /roadmap (now a 307 redirect), so
// there is no sweep results table left to capture.

test("MONEY SHOT 02 — agent controls running", async ({ page }, testInfo) => {
  await page.route("**/api/agent/status", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(STATUS_RUNNING),
    });
  });
  await page.route("**/api/state/stream**", (route) => route.abort());

  await gotoLiveDashboard(page);
  await expect(page.getByTestId("agent-controls")).toHaveAttribute(
    "data-status",
    "running",
    { timeout: 5_000 },
  );

  await page.evaluate(() => window.scrollTo(0, 0));
  const controls = page.getByTestId("agent-controls");
  await controls.scrollIntoViewIfNeeded();
  const suffix = testInfo.project.name === "mobile" ? "mobile" : "desktop";
  await controls.screenshot({
    path: path.join(
      SCREENSHOT_DIR,
      `money_shot_02_agent_controls_running_${suffix}.png`,
    ),
  });
});

test("MONEY SHOT 03 — reflection feed with 3+ entries", async ({ page }, testInfo) => {
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
  await expect(page.getByTestId("reflection-row-2")).toBeVisible({
    timeout: 8_000,
  });
  await feed.scrollIntoViewIfNeeded();
  const suffix = testInfo.project.name === "mobile" ? "mobile" : "desktop";
  await feed.screenshot({
    path: path.join(
      SCREENSHOT_DIR,
      `money_shot_03_reflection_feed_${suffix}.png`,
    ),
  });
});

test("MONEY SHOT 04 — proposal review empty state", async ({ page }, testInfo) => {
  await page.route("**/api/state/stream**", (route) => route.abort());
  await page.route("**/api/agent/status", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(STATUS_RUNNING),
    });
  });

  await gotoLiveDashboard(page);
  const review = page.getByTestId("proposal-review");
  await expect(page.getByTestId("proposal-review-empty")).toBeVisible();
  await review.scrollIntoViewIfNeeded();
  const suffix = testInfo.project.name === "mobile" ? "mobile" : "desktop";
  await review.screenshot({
    path: path.join(
      SCREENSHOT_DIR,
      `money_shot_04_proposal_review_empty_${suffix}.png`,
    ),
  });
});

test("MONEY SHOT 05 — full dashboard composite", async ({ page }, testInfo) => {
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
  await expect(page.getByTestId("agent-controls")).toHaveAttribute(
    "data-status",
    "running",
    { timeout: 5_000 },
  );
  await expect(page.getByTestId("reflection-row-2")).toBeVisible({
    timeout: 8_000,
  });

  const suffix = testInfo.project.name === "mobile" ? "mobile" : "desktop";
  await page.screenshot({
    path: path.join(SCREENSHOT_DIR, `money_shot_05_full_dashboard_${suffix}.png`),
    fullPage: true,
  });
});
