import { expect, test } from "@playwright/test";

/**
 * Dashboard smoke — T-D-002 acceptance gate.
 *
 * We mock the WS via two paths:
 *   1. NEXT_PUBLIC_WS_URL is unset in the production build, so the
 *      WsBootstrap component bails early and the components render
 *      their "WS not yet connected" loading skeletons.
 *   2. We additionally inject state into the Zustand store via a
 *      window-global escape hatch so the smoke can also verify the
 *      "frames received" rendering path.
 *
 * The mobile screenshot at 375 px is the demo-team-facing artefact.
 */

test.describe("Dashboard root — first paint", () => {
  test("renders Vitals + DualEngine + Playback consciousness on mobile", async ({ page }) => {
    await page.goto("/");

    // PLAYBACK takes over the viewport on first load (the Phase 2 Day 4
    // demo arc kicks off automatically). Press Esc to drop into LIVE so
    // we can verify the underlying panel layout.
    await expect(page.getByTestId("playback-takeover")).toBeVisible();
    await page.screenshot({
      path: "screenshots/T-D-002/01-playback-mobile.png",
      fullPage: true,
    });

    await page.keyboard.press("Escape");
    await expect(page.getByTestId("consciousness-live-stub")).toBeVisible();
    await expect(page.getByTestId("vitals-panel")).toBeVisible();
    await expect(page.getByTestId("dual-engine-meter")).toBeVisible();

    await page.screenshot({
      path: "screenshots/T-D-002/02-live-panels-mobile.png",
      fullPage: true,
    });
  });

  test("shows loading skeletons when no WS env is configured", async ({ page }) => {
    await page.goto("/");
    await page.keyboard.press("Escape");

    const vitals = page.getByTestId("vitals-panel");
    const meter = page.getByTestId("dual-engine-meter");
    await expect(vitals).toHaveAttribute("data-loading", "true");
    await expect(meter).toHaveAttribute("data-loading", "true");
  });

  test("renders projected vitals + weights after mock frames are injected", async ({ page }) => {
    // Inject the mocks BEFORE the page boots so the very first render
    // sees state. The dashboard exposes a tiny escape hatch on `window`
    // for exactly this purpose — Playwright + Storybook + manual QA all
    // use the same seam.
    await page.addInitScript(() => {
      (window as unknown as {
        __GENESIS_MOCK_WS__?: unknown[];
      }).__GENESIS_MOCK_WS__ = [
        {
          kind: "vitals",
          ts: "2026-05-21T12:00:00Z",
          seq: 1,
          payload: {
            breath: 72,
            bankroll: 156.5,
            countdown_s: 88,
            gas_per_min: 0.11,
            phase: "PHASE_2_APPRENTICE",
          },
        },
        {
          kind: "weights_updated",
          ts: "2026-05-21T12:00:00Z",
          seq: 2,
          weights: {
            w_r: 0.35,
            w_s: 0.65,
            alpha: 0.62,
            beta: 0.4,
            rho: 0.05,
          },
        },
        {
          kind: "thought",
          ts: "2026-05-21T12:00:01Z",
          seq: 3,
          text: "I see Twitter ramping. I should not chase.",
        },
      ];
    });

    await page.goto("/");
    // Diagnostic — verify WsBootstrap saw the mocks.
    await page.waitForFunction(
      () =>
        (window as unknown as { __GENESIS_MOCK_WS_SEEN__?: number })
          .__GENESIS_MOCK_WS_SEEN__ !== undefined,
      undefined,
      { timeout: 5_000 },
    );
    const seen = await page.evaluate(
      () =>
        (window as unknown as { __GENESIS_MOCK_WS_SEEN__?: number })
          .__GENESIS_MOCK_WS_SEEN__,
    );
    expect(seen).toBe(3);

    await page.keyboard.press("Escape"); // exit playback to LIVE

    await expect(page.getByTestId("vitals-panel")).not.toHaveAttribute(
      "data-loading",
      "true",
    );
    await expect(page.getByTestId("vitals-bankroll-value")).toContainText(
      "$156.50",
    );
    await expect(page.getByTestId("dual-engine-meter")).not.toHaveAttribute(
      "data-loading",
      "true",
    );
    await expect(page.getByTestId("dual-engine-beta-value")).toContainText("0.40");

    await page.screenshot({
      path: "screenshots/T-D-002/03-live-with-data-mobile.png",
      fullPage: true,
    });
  });
});
