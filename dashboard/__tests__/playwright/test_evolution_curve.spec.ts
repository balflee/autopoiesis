import { expect, test } from "@playwright/test";

/**
 * Playwright smoke — EvolutionCurve (T-D-003).
 *
 * Mocks the WS via the `window.__GENESIS_MOCK_WS__` seam so the
 * dashboard boots with a full sprint_4 state vector:
 *   - vitals frame so VitalsPanel exits its skeleton
 *   - weights frames (one frozen β, one unfrozen β) so the β₁ marker draws
 *   - phase_transition frame so the P1→P2 marker draws
 *   - decision_feed frame with 3 settled rows (2 WIN, 1 LOSS) so the
 *     win-rate readout reads 67 %
 *
 * Captures a mobile screenshot at 375 px (the demo team's surface).
 */

test.describe("EvolutionCurve smoke", () => {
  test("renders win-rate, β₁ marker, and phase marker after frames are injected", async ({ page }) => {
    await page.addInitScript(() => {
      (window as unknown as { __GENESIS_MOCK_WS__?: unknown[] }).__GENESIS_MOCK_WS__ = [
        {
          kind: "vitals",
          ts: "2026-05-21T12:00:00Z",
          seq: 1,
          payload: {
            breath: 70,
            bankroll: 180,
            countdown_s: 60,
            gas_per_min: 0.1,
            phase: "PHASE_2_APPRENTICE",
          },
        },
        {
          kind: "weights_updated",
          ts: "2026-05-21T11:55:00Z",
          seq: 2,
          weights: { w_r: 1.0, w_s: 0.0, alpha: 0.5, beta: 0, rho: 0 },
        },
        {
          kind: "weights_updated",
          ts: "2026-05-21T12:00:00Z",
          seq: 3,
          weights: { w_r: 0.6, w_s: 0.4, alpha: 0.5, beta: 0.3, rho: 0.1 },
        },
        {
          kind: "phase_transition",
          ts: "2026-05-21T12:00:00Z",
          seq: 4,
          payload: {
            from: "PHASE_1_INFANCY",
            to: "PHASE_2_APPRENTICE",
            reason: "β₁ unfrozen at Phase 2 boundary",
          },
        },
        {
          kind: "decision_feed",
          ts: "2026-05-21T12:05:00Z",
          seq: 5,
          entries: [
            {
              id: "d1",
              ts: "2026-05-21T12:00:00Z",
              action: "BET",
              side: "LAL ML",
              size_usd: 50,
              result: "WIN",
              pnl_usd: 47.6,
            },
            {
              id: "d2",
              ts: "2026-05-21T12:02:00Z",
              action: "BET",
              side: "BOS ML",
              size_usd: 50,
              result: "LOSS",
              pnl_usd: -50,
            },
            {
              id: "d3",
              ts: "2026-05-21T12:04:00Z",
              action: "BET",
              side: "GSW ML",
              size_usd: 50,
              result: "WIN",
              pnl_usd: 47.6,
            },
          ],
        },
      ];
    });

    await page.goto("/");
    await page.keyboard.press("Escape"); // exit PLAYBACK

    const curve = page.getByTestId("evolution-curve");
    await expect(curve).toBeVisible();
    await expect(curve).toHaveAttribute("data-loading", "false");

    await expect(page.getByTestId("evolution-curve-win-rate-readout")).toContainText(
      "67%",
    );
    await expect(page.getByTestId("evolution-curve-beta-marker")).toBeVisible();
    await expect(page.getByTestId("evolution-curve-phase-marker")).toBeVisible();

    await page.screenshot({
      path: "screenshots/T-D-003/01-evolution-curve.png",
      fullPage: true,
    });
  });
});
