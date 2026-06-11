import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * tests/playwright/death_watch.spec.ts — T-D-007 acceptance.
 *
 * Verifies the Death Watch border + countdown widget surface:
 *
 *   1. Border is hidden when BREATH ≥ threshold (default 10 %).
 *   2. Border appears when BREATH < threshold (via vitals push).
 *   3. Threshold is configurable via the window override
 *      `__GENESIS_DEATH_WATCH_THRESHOLD__` (Playwright test seam).
 *   4. CountdownWidget tier transitions fire deterministically at the
 *      10 min, 5 min, and 1 min boundaries (driven by breath/burn).
 *   5. AAA-style contrast — assert the loss-red token (#E63946) is
 *      applied to the value text in the critical / imminent tiers so
 *      the demo audience can read it from the back of the room.
 *
 * The spec MIRRORS at `tests/playwright/death_watch.spec.ts` so the
 * orchestrator's playwright_smoke gate (which references the canonical
 * `tests/` path) picks it up alongside the per-package runner.
 */

const SCREENSHOT_DIR = "screenshots/T-D-007";

test.beforeAll(() => {
  fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
});

test.describe("DeathWatchBorder — visibility at threshold", () => {
  test("hidden above threshold, visible after a sub-threshold vitals frame", async ({
    page,
  }) => {
    // Seed mock WS with a HEALTHY breath frame (60 %), well above the
    // 10 % threshold. Border should stay hidden.
    await page.addInitScript(() => {
      (
        window as unknown as { __GENESIS_MOCK_WS__?: unknown[] }
      ).__GENESIS_MOCK_WS__ = [
        {
          kind: "vitals",
          ts: "2026-05-22T03:50:00Z",
          seq: 1,
          payload: {
            breath: 60,
            bankroll: 80,
            countdown_s: 120,
            gas_per_min: 0.5,
            phase: "PHASE_2_APPRENTICE",
          },
        },
      ];
    });

    await page.goto("/");
    await page.keyboard.press("Escape"); // exit PLAYBACK takeover

    const border = page.getByTestId("death-watch-border");
    await expect(border).toHaveAttribute("data-visible", "false");

    // Push a sub-threshold breath frame and assert the border flips on
    // within one render.
    await page.evaluate(() => {
      (
        window as unknown as { __GENESIS_PUSH_WS__?: (m: unknown) => void }
      ).__GENESIS_PUSH_WS__?.({
        kind: "vitals",
        ts: "2026-05-22T03:55:00Z",
        seq: 2,
        payload: {
          breath: 8.4,
          bankroll: 80,
          countdown_s: 30,
          gas_per_min: 1,
          phase: "PHASE_3_MASTER",
        },
      });
    });

    await expect(border).toHaveAttribute("data-visible", "true");
    await expect(border).toHaveAttribute("data-breath-pct", "8.4");

    await page.screenshot({
      path: path.join(SCREENSHOT_DIR, "01-border-visible-below-threshold.png"),
      fullPage: true,
    });
  });

  test("threshold honours the __GENESIS_DEATH_WATCH_THRESHOLD__ window override (env-equivalent test seam)", async ({
    page,
  }) => {
    // Override the trigger to 30 % so a breath value the production
    // build would consider "safe" (25 %) trips the border.
    await page.addInitScript(() => {
      (
        window as unknown as { __GENESIS_DEATH_WATCH_THRESHOLD__?: number }
      ).__GENESIS_DEATH_WATCH_THRESHOLD__ = 30;
      (
        window as unknown as { __GENESIS_MOCK_WS__?: unknown[] }
      ).__GENESIS_MOCK_WS__ = [
        {
          kind: "vitals",
          ts: "2026-05-22T03:50:00Z",
          seq: 1,
          payload: {
            breath: 25,
            bankroll: 80,
            countdown_s: 60,
            gas_per_min: 0.5,
            phase: "PHASE_2_APPRENTICE",
          },
        },
      ];
    });

    await page.goto("/");
    await page.keyboard.press("Escape");

    const border = page.getByTestId("death-watch-border");
    await expect(border).toHaveAttribute("data-visible", "true");
    await expect(border).toHaveAttribute("data-threshold-pct", "30");
  });
});

test.describe("CountdownWidget — tier transitions at 10/5/1-min boundaries", () => {
  /**
   * Helper: boot the dashboard with vitals fixed at (breath, burn_rate)
   * so that elapsed time is irrelevant and the countdown widget reads
   * deterministically. Returns the CountdownWidget locator and the
   * helper to push subsequent vitals frames.
   */
  async function boot(
    page: import("@playwright/test").Page,
    breath: number,
    burnRate: number,
  ) {
    await page.addInitScript(
      ({ b, r }) => {
        (
          window as unknown as { __GENESIS_DEATH_WATCH_THRESHOLD__?: number }
        ).__GENESIS_DEATH_WATCH_THRESHOLD__ = 100; // always visible
        (
          window as unknown as { __GENESIS_MOCK_WS__?: unknown[] }
        ).__GENESIS_MOCK_WS__ = [
          {
            kind: "vitals",
            ts: "2026-05-22T03:00:00Z",
            seq: 1,
            payload: {
              breath: b,
              bankroll: 100,
              countdown_s: 60,
              gas_per_min: r,
              phase: "PHASE_3_MASTER",
            },
          },
          {
            kind: "energy_threshold_crossed",
            ts: "2026-05-22T04:00:00Z",
            seq: 2,
            energy_pct: b,
            threshold_pct: 10,
            direction: "below",
          },
        ];
      },
      { b: breath, r: burnRate },
    );
    await page.goto("/");
    await page.keyboard.press("Escape");
    return page.getByTestId("countdown-widget");
  }

  test("safe tier above 10 min (breath=11 / burn=1 → 11:00)", async ({
    page,
  }) => {
    const widget = await boot(page, 11, 1);
    await expect(widget).toHaveAttribute("data-tier", "safe");
    await expect(widget).toHaveAttribute("data-formatted", /^11:00|10:5/);
  });

  test("warning tier at 10 min boundary (breath=10 / burn=1 → exactly 10:00)", async ({
    page,
  }) => {
    const widget = await boot(page, 10, 1);
    // The 1 s tick can shave a second by render time. Accept either
    // 10:00 or 09:5x — both belong to the warning tier.
    await expect(widget).toHaveAttribute("data-tier", "warning");
    const seconds = await widget.getAttribute("data-seconds");
    expect(Number(seconds)).toBeGreaterThanOrEqual(595);
    expect(Number(seconds)).toBeLessThanOrEqual(600);
  });

  test("critical tier at 5 min boundary (breath=5 / burn=1 → ≈ 5:00)", async ({
    page,
  }) => {
    const widget = await boot(page, 5, 1);
    await expect(widget).toHaveAttribute("data-tier", "critical");
    const seconds = await widget.getAttribute("data-seconds");
    expect(Number(seconds)).toBeGreaterThanOrEqual(295);
    expect(Number(seconds)).toBeLessThanOrEqual(300);
  });

  test("imminent tier at 1 min boundary (breath=1 / burn=1 → ≈ 1:00)", async ({
    page,
  }) => {
    const widget = await boot(page, 1, 1);
    await expect(widget).toHaveAttribute("data-tier", "imminent");
    const seconds = await widget.getAttribute("data-seconds");
    expect(Number(seconds)).toBeGreaterThanOrEqual(55);
    expect(Number(seconds)).toBeLessThanOrEqual(60);

    // Computed colour must be the loss-red token (PRD §8 palette).
    const value = page.getByTestId("countdown-widget-value");
    const colour = await value.evaluate(
      (el) => getComputedStyle(el as HTMLElement).color,
    );
    // rgb(230, 57, 70) = #E63946; allow either lowercase rgb or rgba.
    expect(colour.replace(/\s+/g, "")).toMatch(/^rgba?\(230,57,70/);

    await page.screenshot({
      path: path.join(SCREENSHOT_DIR, "02-countdown-imminent.png"),
      fullPage: true,
    });
  });

  test("expired tier when cause_of_death lands (breath=0 → 00:00)", async ({
    page,
  }) => {
    await page.addInitScript(() => {
      (
        window as unknown as { __GENESIS_DEATH_WATCH_THRESHOLD__?: number }
      ).__GENESIS_DEATH_WATCH_THRESHOLD__ = 100;
      (
        window as unknown as { __GENESIS_MOCK_WS__?: unknown[] }
      ).__GENESIS_MOCK_WS__ = [
        {
          kind: "vitals",
          ts: "2026-05-22T03:00:00Z",
          seq: 1,
          payload: {
            breath: 0,
            bankroll: 100,
            countdown_s: 0,
            gas_per_min: 1,
            phase: "PHASE_4_TERMINAL",
          },
        },
        {
          kind: "energy_threshold_crossed",
          ts: "2026-05-22T04:00:00Z",
          seq: 2,
          energy_pct: 0,
          threshold_pct: 10,
          direction: "below",
        },
        {
          kind: "death",
          ts: "2026-05-22T04:00:01Z",
          seq: 3,
          cause: "ENERGY_DEPLETED",
        },
      ];
    });
    await page.goto("/");
    await page.keyboard.press("Escape");

    const widget = page.getByTestId("countdown-widget");
    await expect(widget).toHaveAttribute("data-tier", "expired");
    await expect(widget).toHaveAttribute("data-formatted", "00:00");
  });
});
