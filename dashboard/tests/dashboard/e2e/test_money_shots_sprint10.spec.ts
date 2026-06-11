import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * Money-Shot screenshots for T-D-013 sprint_10 (Day 6) visual sign-off.
 *
 * needs_human_review=true on this task — the User signs off on the four
 * shots called out in the brief BEFORE MARK_COMPLETED. Per the brief
 * acceptance criteria:
 *
 *   1. sprint10_proposal_pending.png         — pending proposal card,
 *                                              full chrome
 *   2. sprint10_proposal_approved.png        — post-approve state (the
 *                                              "card faded + moved to
 *                                              history" branch)
 *   3. sprint10_proposal_history.png         — history tab with a mix of
 *                                              approved + rejected rows
 *   4. sprint10_weight_delta_applied.png     — BREATH/vitals ticker
 *                                              after a weight_delta has
 *                                              been promoted to runtime
 *
 * All four shots are captured at >= 1440px wide (per brief). The desktop
 * Playwright project ships at 1280; we override viewport per-test via
 * `page.setViewportSize({ width: 1440, ... })` so a single project run
 * produces the demo-grade shots without churning playwright.config.ts.
 *
 * Output committed to `dashboard/public/screenshots/` (per brief). The
 * `dashboard/screenshots/T-D-013/` location is kept for the harness
 * report folder convention but the canonical sign-off PNGs live in
 * `public/screenshots/` so they ship with the Next.js build and the
 * User can pin them in a browser tab while reviewing.
 *
 * IMPORTANT — fixtures, not live data: the screenshots are intentionally
 * captured from a deterministic seed so the User sees the same pixels on
 * every retake. The live `/api/proxy/...` round-trip is exercised by
 * `test_live_smoke_post_deploy.spec.ts` (env-gated, @live).
 */

const PUBLIC_DIR = "public/screenshots";
const REPORT_DIR = "screenshots/T-D-013";

test.beforeAll(() => {
  fs.mkdirSync(PUBLIC_DIR, { recursive: true });
  fs.mkdirSync(REPORT_DIR, { recursive: true });
});

// Run only on the desktop project — mobile (375 px) is below the brief's
// 1440 px floor and these shots are explicitly the wide-projector cuts.
// Skip per-test via `test.skip(condition, reason)` so this works whether
// the runner is invoked with `--project=desktop` (the intended path) or
// the default all-projects sweep.
test.beforeEach(({}, testInfo) => {
  test.skip(
    testInfo.project.name === "mobile",
    "Money-Shot wide cuts run on the desktop project only (1440+ px viewport).",
  );
});

const STATUS_RUNNING = {
  phase: "PHASE_2_APPRENTICE",
  breath: 78.4,
  last_tick_ts: "2026-05-28T05:20:00Z",
  current_weights: { w_r: 0.5, w_s: 0.5, alpha: 0.56, beta: 0.5, rho: 0.0 },
  llm_cost_usd_this_month: 0.42,
  pending_proposals_count: 1,
  running: true,
  run_id: "run_sprint10_t_d_013",
};

const STATUS_POST_WEIGHT_DELTA = {
  ...STATUS_RUNNING,
  // Operator approved the pending weight_delta proposal — alpha_2 moves
  // +0.06 to 0.56 (visible in current_weights below) and breath ticks
  // up as the L3 fold lands.
  breath: 81.6,
  current_weights: { w_r: 0.5, w_s: 0.5, alpha: 0.56, beta: 0.5, rho: 0.0 },
  pending_proposals_count: 0,
  last_tick_ts: "2026-05-28T05:21:08Z",
};

/** Three proposals — one pending, one already-approved, one rejected — so
 *  the History tab shows a meaningful mix of statuses. */
const SEED_MIXED = [
  {
    proposal_id: "p_weight_delta_pending",
    ts: "2026-05-28T05:20:30.000Z",
    kind: "weight_delta",
    rationale:
      "alpha_2 carries the regime; nudge weight up 0.06 to capture it before the regime closes.",
    proposed_change: { key: "alpha_2", delta: 0.06 },
    expected_impact: "+3-5% Sharpe over next 100 ticks",
    confidence_pct: 72,
    requires_human_approval: true,
    status: "pending",
  },
  {
    proposal_id: "p_signal_idea_approved",
    ts: "2026-05-28T04:42:11.000Z",
    kind: "new_signal_idea",
    rationale:
      "smart-money order-flow imbalance crossed +0.18 three ticks in a row; promoting as signal candidate to feed the L1 sentiment engine.",
    proposed_change: {
      signal_id: "smart_money_ofi_5m",
      window: "5m",
      threshold: 0.18,
    },
    expected_impact: "+1.5% win-rate on close-to-close swings",
    confidence_pct: 64,
    requires_human_approval: true,
    status: "approved",
  },
  {
    proposal_id: "p_prompt_tweak_rejected",
    ts: "2026-05-28T03:18:55.000Z",
    kind: "prompt_tweak",
    rationale:
      "reflection prompt is drifting toward over-confident language; trim hedge cues to keep the agent's risk posture conservative.",
    proposed_change: {
      target: "reflection_v2",
      patch:
        "remove the phrase 'I'm confident' from the second-to-last paragraph",
    },
    expected_impact: "−1 standard deviation in confidence_pct noise",
    confidence_pct: 41,
    requires_human_approval: true,
    status: "rejected",
  },
];

const SEED_PENDING_ONLY = [SEED_MIXED[0]];

/** Synchronous-resolve approve hook — the optimistic UI moves the card to
 *  the History tab within 100 ms (the T-D-012 acceptance gate). We resolve
 *  after a small 150 ms delay so the Money Shot captures the "approved"
 *  steady state, not the in-flight spinner. */
async function installApproveSeed(
  page: import("@playwright/test").Page,
  seed: ReadonlyArray<unknown>,
): Promise<void> {
  await page.addInitScript(
    ({ seed }) => {
      const w = window as unknown as {
        __GENESIS_PROPOSAL_SEED__: unknown;
        __GENESIS_PROPOSAL_API__: {
          approveProposal: (id: string) => Promise<unknown>;
          rejectProposal: (id: string, reason?: string) => Promise<unknown>;
        };
      };
      w.__GENESIS_PROPOSAL_SEED__ = seed;
      w.__GENESIS_PROPOSAL_API__ = {
        approveProposal: async (id: string) => {
          await new Promise((r) => setTimeout(r, 150));
          return {
            proposal_id: id,
            status: "approved",
            applied_to_runtime: true,
          };
        },
        rejectProposal: async (id: string, reason?: string) => {
          await new Promise((r) => setTimeout(r, 150));
          return {
            proposal_id: id,
            status: "rejected",
            applied_to_runtime: false,
            reason,
          };
        },
      };
    },
    { seed },
  );
}

/** Inject WS vitals so VitalsPanel renders non-skeleton numerals
 *  (BREATH bar shows a value, weight tuple appears in DualEngineMeter). */
async function installVitalsMock(
  page: import("@playwright/test").Page,
  vitals: {
    breath: number;
    weights: {
      w_r: number;
      w_s: number;
      alpha: number;
      beta: number;
      rho: number;
    };
  },
): Promise<void> {
  await page.addInitScript((v) => {
    (window as unknown as { __GENESIS_MOCK_WS__?: unknown[] }).__GENESIS_MOCK_WS__ = [
      {
        kind: "vitals",
        ts: "2026-05-28T05:20:00Z",
        seq: 1,
        payload: {
          breath: v.breath,
          bankroll: 218.5,
          countdown_s: 92,
          gas_per_min: 0.11,
          phase: "PHASE_2_APPRENTICE",
        },
      },
      {
        kind: "weights_updated",
        ts: "2026-05-28T05:20:00Z",
        seq: 2,
        weights: v.weights,
      },
      {
        kind: "thought",
        ts: "2026-05-28T05:20:01Z",
        seq: 3,
        text: "Nudging alpha_2 — smart-money order-flow imbalance is sustained.",
      },
    ];
  }, vitals);
}

async function stubStatusRoute(
  page: import("@playwright/test").Page,
  body: unknown,
): Promise<void> {
  await page.route("**/api/agent/status", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });
  // Don't let SSE chatter pollute the screenshot.
  await page.route("**/api/state/stream**", (route) => route.abort());
}

async function gotoLiveDashboard(
  page: import("@playwright/test").Page,
): Promise<void> {
  await page.goto("/");
  await expect(page.getByTestId("playback-takeover")).toBeVisible();
  await page.keyboard.press("Escape");
}

async function saveShot(
  locator: import("@playwright/test").Locator | import("@playwright/test").Page,
  name: string,
  opts: { fullPage?: boolean } = {},
): Promise<void> {
  // Belt-and-braces: both report folder and the public/ canonical folder.
  // Brief floor: >= 1440 px wide. Viewport is set to 1440 in each test,
  // and we default to fullPage = true on Page targets so the entire dash
  // column is captured at viewport width. Locator targets pass the option
  // explicitly when needed.
  const isPage = "setViewportSize" in locator;
  const shotOpts =
    isPage && opts.fullPage !== false ? { fullPage: true } : {};
  await locator.screenshot({
    path: path.join(REPORT_DIR, name),
    ...shotOpts,
  });
  await locator.screenshot({
    path: path.join(PUBLIC_DIR, name),
    ...shotOpts,
  });
}

test("MONEY SHOT — sprint10_proposal_pending", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await installApproveSeed(page, SEED_PENDING_ONLY);
  await installVitalsMock(page, {
    breath: STATUS_RUNNING.breath,
    weights: STATUS_RUNNING.current_weights,
  });
  await stubStatusRoute(page, STATUS_RUNNING);

  await gotoLiveDashboard(page);

  const review = page.getByTestId("proposal-review");
  await expect(review).toBeVisible();
  await expect(review).toHaveAttribute("data-tab", "pending");
  await expect(page.getByTestId("proposal-review-pending-list")).toBeVisible();
  await review.scrollIntoViewIfNeeded();

  // Brief: >= 1440 px wide. Take a full-page page-level screenshot so the
  // ProposalReview card lands in its dashboard context AND the image is
  // 1440 px wide (the viewport floor set above).
  await saveShot(page, "sprint10_proposal_pending.png");
});

test("MONEY SHOT — sprint10_proposal_approved", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await installApproveSeed(page, SEED_PENDING_ONLY);
  await installVitalsMock(page, {
    breath: STATUS_POST_WEIGHT_DELTA.breath,
    weights: STATUS_POST_WEIGHT_DELTA.current_weights,
  });
  await stubStatusRoute(page, STATUS_POST_WEIGHT_DELTA);

  await gotoLiveDashboard(page);

  const review = page.getByTestId("proposal-review");
  await expect(review).toBeVisible();

  // Approve the lone pending card. Optimistic UI moves it to History.
  const approveBtn = review.getByTestId("proposal-card-0-approve");
  await approveBtn.click();

  // Switch to history tab so the approved card is in frame.
  await page.getByTestId("proposal-review-tab-history").click();
  await expect(page.getByTestId("proposal-review-history-list")).toBeVisible({
    timeout: 5_000,
  });
  await review.scrollIntoViewIfNeeded();

  await saveShot(page, "sprint10_proposal_approved.png");
});

test("MONEY SHOT — sprint10_proposal_history", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await installApproveSeed(page, SEED_MIXED);
  await installVitalsMock(page, {
    breath: STATUS_POST_WEIGHT_DELTA.breath,
    weights: STATUS_POST_WEIGHT_DELTA.current_weights,
  });
  await stubStatusRoute(page, STATUS_POST_WEIGHT_DELTA);

  await gotoLiveDashboard(page);

  const review = page.getByTestId("proposal-review");
  await expect(review).toBeVisible();
  await page.getByTestId("proposal-review-tab-history").click();
  await expect(page.getByTestId("proposal-review-history-list")).toBeVisible();
  await review.scrollIntoViewIfNeeded();

  await saveShot(page, "sprint10_proposal_history.png");
});

test("MONEY SHOT — sprint10_weight_delta_applied", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  // After the operator approved the weight_delta, the runtime breath +
  // current_weights tuple have advanced. Capture VitalsPanel +
  // DualEngineMeter together so the operator sees the BREATH ticker AND
  // the alpha-component value side-by-side.
  await installVitalsMock(page, {
    breath: STATUS_POST_WEIGHT_DELTA.breath,
    weights: STATUS_POST_WEIGHT_DELTA.current_weights,
  });
  await stubStatusRoute(page, STATUS_POST_WEIGHT_DELTA);

  await gotoLiveDashboard(page);

  await expect(page.getByTestId("vitals-panel")).not.toHaveAttribute(
    "data-loading",
    "true",
  );
  await expect(page.getByTestId("dual-engine-meter")).not.toHaveAttribute(
    "data-loading",
    "true",
  );

  // Clip the top band — VitalsPanel + nav + DualEngine row — so the shot
  // is the demo-grade "weight delta applied" hero, not a full-page
  // screenshot that would dwarf the actual numerals.
  await page.evaluate(() => window.scrollTo(0, 0));
  await saveShot(page, "sprint10_weight_delta_applied.png");
});
