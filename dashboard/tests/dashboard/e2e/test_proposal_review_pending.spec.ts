import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * ProposalReview PENDING tab — T-D-012 sprint_10.
 *
 * The brief locks the per-card surface: kind badge + rationale (≤3 lines
 * clamp) + proposed_change JSON (syntax-highlighted, collapsible >5 lines)
 * + expected_impact + confidence bar + ts + Approve/Reject buttons.
 *
 * Strategy: seed the panel with three proposals via the
 * `__GENESIS_PROPOSAL_SEED__` window hook so the test is hermetic against
 * the live SSE stream. The seed must be planted BEFORE React mounts;
 * `page.addInitScript` runs before any page script, satisfying that
 * invariant.
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
  pending_proposals_count: 3,
  running: true,
  run_id: "run_pending",
};

const SEED = [
  {
    proposal_id: "p_weight_delta_001",
    ts: "2026-05-27T18:30:00.000Z",
    kind: "weight_delta",
    rationale:
      "Over the last 100 ticks alpha_2 has been the strongest predictor (corr 0.42 with realised PnL) but its weight only drifted 0.31 → 0.34. Propose nudge to 0.40 to capture the regime.",
    proposed_change: { key: "alpha_2", delta: 0.06 },
    expected_impact: "+3-5% Sharpe over next 100 ticks",
    confidence_pct: 65,
    requires_human_approval: true,
    status: "pending",
  },
  {
    proposal_id: "p_new_signal_002",
    ts: "2026-05-27T19:00:00.000Z",
    kind: "new_signal_idea",
    rationale:
      "Reflections over the last 5 trigger fires mention 'tournament fatigue' as an unmodeled factor. Propose new tennis_fatigue engine fed by days_since_last_match * matches_in_last_14d.",
    proposed_change: {
      name: "tennis_fatigue",
      primary_features: ["days_since_last_match", "matches_in_last_14d"],
      fusion_layer: "alpha_4",
    },
    expected_impact: "Unknown — needs sprint_11 backtest",
    confidence_pct: 35,
    requires_human_approval: true,
    status: "pending",
  },
  {
    proposal_id: "p_prompt_tweak_003",
    ts: "2026-05-27T20:00:00.000Z",
    kind: "prompt_tweak",
    rationale:
      "The L1 sentiment prompt currently asks for a single score 0..1; observation suggests the model is anchoring on 0.5 when uncertain. Propose adding an explicit 'unsure' bucket that maps to confidence=0.",
    proposed_change: {
      prompt_file: "agent/llm/prompts/sentiment.txt",
      diff_summary: "add explicit 'unsure' output category",
    },
    expected_impact: "Reduce mean L1 confidence by ~0.15",
    confidence_pct: 55,
    requires_human_approval: true,
    status: "pending",
  },
];

async function setupSeed(page: import("@playwright/test").Page): Promise<void> {
  await page.addInitScript((seed) => {
    (window as unknown as { __GENESIS_PROPOSAL_SEED__: unknown }).__GENESIS_PROPOSAL_SEED__ = seed;
  }, SEED);
}

async function gotoLiveDashboard(
  page: import("@playwright/test").Page,
): Promise<void> {
  await page.goto("/");
  await expect(page.getByTestId("playback-takeover")).toBeVisible();
  await page.keyboard.press("Escape");
}

test.describe("ProposalReview — pending tab", () => {
  test("3 pending → renders 3 cards newest-first with kind badge + impact + confidence bar", async ({ page }, testInfo) => {
    await setupSeed(page);
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
    await expect(review).toHaveAttribute("data-empty", "false");
    await expect(review).toHaveAttribute("data-tab", "pending");

    // Tab counters reflect 3 pending / 0 history.
    await expect(page.getByTestId("proposal-review-tab-pending")).toContainText(
      /pending · 3/,
    );
    await expect(page.getByTestId("proposal-review-tab-history")).toContainText(
      /history · 0/,
    );

    // Three cards visible.
    const list = page.getByTestId("proposal-review-pending-list");
    await expect(list).toBeVisible();
    await expect(page.getByTestId("proposal-card-0")).toBeVisible();
    await expect(page.getByTestId("proposal-card-1")).toBeVisible();
    await expect(page.getByTestId("proposal-card-2")).toBeVisible();

    // Newest-first ordering — prompt_tweak (20:00) is on top.
    await expect(page.getByTestId("proposal-card-0-kind")).toContainText(
      /prompt tweak/i,
    );
    await expect(page.getByTestId("proposal-card-2-kind")).toContainText(
      /weight delta/i,
    );

    // Confidence bar renders with the aria-valuenow we seeded.
    const topBar = page.getByTestId("proposal-card-0-bar");
    await expect(topBar).toHaveAttribute("aria-valuenow", "55");

    // Expected impact chip visible.
    await expect(page.getByTestId("proposal-card-0-impact")).toContainText(
      /reduce mean l1 confidence/i,
    );

    // Approve + Reject buttons have aria-labels containing the proposal summary.
    const approveBtn = page.getByTestId("proposal-card-0-approve");
    const rejectBtn = page.getByTestId("proposal-card-0-reject");
    await expect(approveBtn).toBeVisible();
    await expect(rejectBtn).toBeVisible();
    const approveAria = await approveBtn.getAttribute("aria-label");
    expect(approveAria).toMatch(/^approve proposal:/i);
    const rejectAria = await rejectBtn.getAttribute("aria-label");
    expect(rejectAria).toMatch(/^reject proposal:/i);

    // History tab when clicked is empty.
    await page.getByTestId("proposal-review-tab-history").click();
    await expect(review).toHaveAttribute("data-tab", "history");
    await expect(
      page.getByTestId("proposal-review-history-empty"),
    ).toBeVisible();

    // Money-shot screenshot of the pending tab.
    await page.getByTestId("proposal-review-tab-pending").click();
    const suffix = testInfo.project.name === "mobile" ? "mobile" : "desktop";
    await page.screenshot({
      path: path.join(
        SCREENSHOT_DIR,
        `02-proposal-review-pending-${suffix}.png`,
      ),
      fullPage: true,
    });
  });
});
