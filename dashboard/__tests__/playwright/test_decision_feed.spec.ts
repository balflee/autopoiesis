import { expect, test } from "@playwright/test";

/**
 * Playwright smoke — DecisionFeed (T-D-003).
 *
 * Two assertions:
 *   1. Initial render of 3 rows from a single decision_feed frame
 *   2. Click-to-expand reveals the reasoning + reflection detail
 *
 * The LLMActivationOverlay is also exercised here — pushing the
 * `llm_activated` frame in the mock bucket should fire the overlay
 * exactly once (the test verifies content visibility right after load).
 */

test.describe("DecisionFeed smoke", () => {
  test("renders rows and expands detail on click", async ({ page }) => {
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
          kind: "decision_feed",
          ts: "2026-05-21T12:05:00Z",
          seq: 2,
          entries: [
            {
              id: "d1",
              ts: "2026-05-21T12:00:00Z",
              action: "BET",
              side: "LAL ML",
              size_usd: 50,
              result: "WIN",
              pnl_usd: 47.6,
              reasoning: "Sentiment ramped 35% in 10 min; line still soft.",
              reflection: "Held discipline on size despite urge to overlever.",
            },
            {
              id: "d2",
              ts: "2026-05-21T12:02:00Z",
              action: "NO_BET",
              side: "BOS ML",
              result: "PENDING",
            },
            {
              id: "d3",
              ts: "2026-05-21T12:04:00Z",
              action: "BET",
              side: "GSW ML",
              size_usd: 25,
              result: "PENDING",
            },
          ],
        },
      ];
    });

    await page.goto("/");
    await page.keyboard.press("Escape");

    const feed = page.getByTestId("decision-feed");
    await expect(feed).toBeVisible();
    await expect(page.getByTestId("decision-feed-count")).toContainText("3 rows");

    // Newest-first — d3 is at top.
    const rows = feed.getByTestId("decision-feed-row");
    await expect(rows).toHaveCount(3);
    await expect(rows.first()).toHaveAttribute("data-id", "d3");

    // Click d1 (the WIN row with reasoning + reflection).
    const winRow = feed.locator('[data-id="d1"]');
    await winRow.getByTestId("decision-feed-row-toggle").click();
    await expect(winRow.getByTestId("decision-feed-row-detail")).toContainText(
      "Sentiment ramped",
    );
    await expect(winRow.getByTestId("decision-feed-row-detail")).toContainText(
      "Held discipline",
    );

    await page.screenshot({
      path: "screenshots/T-D-003/02-decision-feed.png",
      fullPage: true,
    });
  });

  test("LLM activation overlay fires exactly once when llm_activated is in the mock bucket", async ({ page }) => {
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
          kind: "llm_activated",
          ts: "2026-05-21T12:00:01Z",
          seq: 2,
          note: "β₁ unfrozen at Phase 2 boundary",
        },
        // Replay — the dedup-by-seq in WsClient also discards this, but
        // even if it didn't the store's llmActivatedShown latch would.
        {
          kind: "llm_activated",
          ts: "2026-05-21T12:00:02Z",
          seq: 3,
        },
      ];
    });

    await page.goto("/");

    // The overlay is full-screen + above PLAYBACK; we read the content
    // synchronously then wait for it to retract.
    await expect(page.getByTestId("llm-activation-overlay-content")).toBeVisible();
    await expect(page.getByTestId("llm-activation-overlay-headline")).toContainText(
      /sentient engine awakening/i,
    );

    // Wait for fade-out — the component clears `renderOverlay` at
    // 1500 ms.
    await page.waitForTimeout(2000);
    const root = page.getByTestId("llm-activation-overlay-root");
    await expect(root).toHaveAttribute("data-overlay-rendering", "false");
    await expect(root).toHaveAttribute("data-overlay-shown", "true");

    // Push a fresh llm_activated through the live escape hatch the
    // hook exposes. The latch should swallow it — no re-render.
    await page.evaluate(() => {
      (window as unknown as {
        __GENESIS_PUSH_WS__?: (m: unknown) => void;
      }).__GENESIS_PUSH_WS__?.({
        kind: "llm_activated",
        ts: "2026-05-21T12:00:10Z",
        seq: 99,
      });
    });
    await page.waitForTimeout(200);
    await expect(root).toHaveAttribute("data-overlay-rendering", "false");
    await expect(page.getByTestId("llm-activation-overlay-content")).toHaveCount(0);
  });
});
