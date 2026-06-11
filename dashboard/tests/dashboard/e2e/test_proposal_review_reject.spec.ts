import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * ProposalReview REJECT flow — T-D-012 sprint_10.
 *
 * Acceptance criterion (brief): "Reject → textarea modal for reason →
 * submit. Reason persisted." Coverage:
 *
 *   1. Reject opens a modal with a labelled textarea + submit / cancel.
 *   2. ESC closes the modal without calling the backend.
 *   3. Submitting with a non-empty reason forwards it to the API hook;
 *      the card moves to the History tab with `data-status=rejected`.
 *   4. The reason argument the hook received is the trimmed string the
 *      operator typed.
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
  pending_proposals_count: 1,
  running: true,
  run_id: "run_reject",
};

const SEED = [
  {
    proposal_id: "p_prompt_tweak_reject",
    ts: "2026-05-27T20:00:00.000Z",
    kind: "prompt_tweak",
    rationale:
      "The L1 sentiment prompt anchors on 0.5; propose adding an 'unsure' category that maps to confidence 0.",
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

async function setupSeed(
  page: import("@playwright/test").Page,
): Promise<void> {
  await page.addInitScript((seed) => {
    const w = window as unknown as {
      __GENESIS_PROPOSAL_SEED__: unknown;
      __GENESIS_PROPOSAL_API__: {
        approveProposal: (id: string) => Promise<unknown>;
        rejectProposal: (id: string, reason?: string) => Promise<unknown>;
      };
      __REJECT_CALLS__: Array<{ id: string; reason: string | undefined }>;
    };
    w.__GENESIS_PROPOSAL_SEED__ = seed;
    w.__REJECT_CALLS__ = [];
    w.__GENESIS_PROPOSAL_API__ = {
      approveProposal: async (id: string) => ({
        proposal_id: id,
        status: "approved",
        applied_to_runtime: true,
      }),
      rejectProposal: async (id: string, reason?: string) => {
        w.__REJECT_CALLS__.push({ id, reason });
        await new Promise((r) => setTimeout(r, 100));
        return { proposal_id: id, status: "rejected", applied_to_runtime: false };
      },
    };
  }, SEED);
}

async function gotoLiveDashboard(
  page: import("@playwright/test").Page,
): Promise<void> {
  await page.goto("/");
  await expect(page.getByTestId("playback-takeover")).toBeVisible();
  await page.keyboard.press("Escape");
}

test.describe("ProposalReview — reject flow", () => {
  test("reject → modal → reason persisted to the API call", async ({ page }, testInfo) => {
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
    await expect(page.getByTestId("proposal-card-0")).toBeVisible();

    // Click Reject — modal opens with a focused textarea.
    await page.getByTestId("proposal-card-0-reject").click();
    const modal = page.getByTestId("proposal-review-reject-modal");
    await expect(modal).toBeVisible();
    await expect(modal).toHaveAttribute("role", "dialog");
    await expect(modal).toHaveAttribute("aria-modal", "true");

    const textarea = page.getByTestId("proposal-review-reject-reason");
    await expect(textarea).toBeFocused();

    // ESC closes the modal without calling the API.
    await page.keyboard.press("Escape");
    await expect(modal).toHaveCount(0);
    let rejectCalls = await page.evaluate(
      () =>
        (window as unknown as {
          __REJECT_CALLS__: Array<{ id: string; reason: string | undefined }>;
        }).__REJECT_CALLS__,
    );
    expect(rejectCalls).toEqual([]);

    // Re-open + type a reason + submit.
    await page.getByTestId("proposal-card-0-reject").click();
    await expect(modal).toBeVisible();
    const REASON = "  waiting on tomorrow's reflection before bumping alpha_2  ";
    await page.getByTestId("proposal-review-reject-reason").fill(REASON);

    // Screenshot the modal before submit so the demo team can sign off.
    const suffix = testInfo.project.name === "mobile" ? "mobile" : "desktop";
    await page.screenshot({
      path: path.join(
        SCREENSHOT_DIR,
        `04-proposal-review-reject-modal-${suffix}.png`,
      ),
      fullPage: true,
    });

    await page.getByTestId("proposal-review-reject-submit").click();
    await expect(modal).toHaveCount(0);

    // History counter ticks up.
    await expect(
      page.getByTestId("proposal-review-tab-history"),
    ).toContainText(/history · 1/, { timeout: 2_000 });
    await expect(
      page.getByTestId("proposal-review-tab-pending"),
    ).toContainText(/pending · 0/);

    // Verify the API hook received the TRIMMED reason.
    rejectCalls = await page.evaluate(
      () =>
        (window as unknown as {
          __REJECT_CALLS__: Array<{ id: string; reason: string | undefined }>;
        }).__REJECT_CALLS__,
    );
    expect(rejectCalls).toHaveLength(1);
    expect(rejectCalls[0]).toEqual({
      id: "p_prompt_tweak_reject",
      reason: REASON.trim(),
    });

    // History tab shows the rejected card with status badge.
    await page.getByTestId("proposal-review-tab-history").click();
    await expect(review).toHaveAttribute("data-tab", "history");
    const historyCard = page.getByTestId("proposal-card-0");
    await expect(historyCard).toBeVisible();
    await expect(historyCard).toHaveAttribute("data-status", "rejected");
    await expect(page.getByTestId("proposal-card-0-status")).toContainText(
      /rejected/i,
    );
  });
});
