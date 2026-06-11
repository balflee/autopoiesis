import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * ProposalReview EMPTY state — T-D-012 sprint_10 (updated from T-D-010).
 *
 * Sprint_10 day 4 closes the L3 demo moment but the panel still has an
 * empty state for the cold-start case: no proposals seen since the
 * dashboard opened. Coverage:
 *
 *   - panel renders "no pending proposals" empty-state on /
 *   - tabs render with counters (`pending · 0`, `history · 0`)
 *   - PRD §12 link is present + has a real href
 *   - panel does NOT crash when /api/state/stream is unreachable
 */

const SCREENSHOT_DIR = "screenshots/T-D-012";

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
  run_id: "run_proposalspec",
};

async function gotoLiveDashboard(
  page: import("@playwright/test").Page,
): Promise<void> {
  await page.goto("/");
  await expect(page.getByTestId("playback-takeover")).toBeVisible();
  await page.keyboard.press("Escape");
}

test.describe("ProposalReview — empty state", () => {
  test("renders the empty state with PRD link when no proposals are streamed", async ({ page }, testInfo) => {
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
    await expect(review).toBeVisible();
    await expect(review).toHaveAttribute("data-empty", "true");
    await expect(review).toHaveAttribute("data-tab", "pending");

    // Tabs render with zero counters.
    const pendingTab = page.getByTestId("proposal-review-tab-pending");
    const historyTab = page.getByTestId("proposal-review-tab-history");
    await expect(pendingTab).toBeVisible();
    await expect(pendingTab).toContainText(/pending · 0/);
    await expect(historyTab).toBeVisible();
    await expect(historyTab).toContainText(/history · 0/);

    // Empty state visible with copy + PRD link.
    const empty = page.getByTestId("proposal-review-empty");
    await expect(empty).toBeVisible();
    await expect(empty).toContainText(/no pending proposals/i);

    const link = page.getByTestId("proposal-review-prd-link");
    await expect(link).toBeVisible();
    const href = await link.getAttribute("href");
    expect(href).toBeTruthy();
    expect(href).not.toBe("#");
    expect(href).toContain("PRD");

    const suffix = testInfo.project.name === "mobile" ? "mobile" : "desktop";
    await page.screenshot({
      path: path.join(SCREENSHOT_DIR, `01-proposal-review-empty-${suffix}.png`),
      fullPage: true,
    });
  });

  test("does not crash the route when the backend is fully unreachable", async ({ page }) => {
    await page.route("**/api/**", (route) => route.abort());
    await gotoLiveDashboard(page);
    await expect(page.getByTestId("proposal-review")).toBeVisible();
    await expect(page.getByTestId("proposal-review-empty")).toBeVisible();
  });
});
