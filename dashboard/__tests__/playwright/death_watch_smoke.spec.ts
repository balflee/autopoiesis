import { expect, test } from "@playwright/test";

/**
 * Death Watch smoke — T-D-004 acceptance gate.
 *
 * Verifies:
 *   1. The Death Watch UI is hidden until a death-watch trigger fires.
 *   2. Once an `energy_threshold_crossed` (below, 10) frame lands, the
 *      DeathWatch surface becomes visible within ~200 ms.
 *   3. The Last-Words typewriter mounts when last_words_emitted lands.
 *   4. Tombstone block + ipfs_degraded badge surface on tombstone_minted.
 *   5. Sticky terminal — after terminal_lucidity_entered + a recovery
 *      vitals frame, DeathWatch remains visible (PRD §6.10).
 *   6. Screenshots at 375px (the demo's mobile target) are captured.
 */

test.describe("Death Watch — full-screen takeover", () => {
  test("triggers within 200ms of energy_threshold_crossed (below 10%)", async ({ page }) => {
    await page.addInitScript(() => {
      (window as unknown as { __GENESIS_MOCK_WS__?: unknown[] }).__GENESIS_MOCK_WS__ = [
        {
          kind: "vitals",
          ts: "2026-05-22T03:50:00Z",
          seq: 1,
          payload: {
            breath: 60,
            bankroll: 80,
            countdown_s: 120,
            gas_per_min: 0.12,
            phase: "PHASE_3_MASTER",
          },
        },
      ];
    });

    await page.goto("/");
    await page.keyboard.press("Escape"); // exit any default playback overlay

    // Sanity: DeathWatch is hidden before the trigger.
    const root = page.getByTestId("death-watch-root");
    await expect(root).toHaveAttribute("data-visible", "false");

    // Inject the trigger event via the same window seam the WS hook
    // exposes and measure end-to-end render latency.
    const ack = await page.evaluate(async () => {
      const t0 = performance.now();
      (
        window as unknown as { __GENESIS_PUSH_WS__?: (m: unknown) => void }
      ).__GENESIS_PUSH_WS__?.({
        kind: "energy_threshold_crossed",
        ts: "2026-05-22T04:00:00Z",
        seq: 2,
        energy_pct: 9.4,
        threshold_pct: 10,
        direction: "below",
      });
      // Wait until React commits the visible attribute.
      const deadline = t0 + 1000;
      while (performance.now() < deadline) {
        const el = document.querySelector('[data-testid="death-watch-root"]');
        if (el?.getAttribute("data-visible") === "true") {
          return performance.now() - t0;
        }
        await new Promise((r) => requestAnimationFrame(r));
      }
      return -1;
    });
    expect(ack).toBeGreaterThanOrEqual(0);
    expect(ack).toBeLessThan(200);

    await expect(root).toHaveAttribute("data-visible", "true");
    await expect(page.getByTestId("death-watch-headline")).toBeVisible();
    await expect(page.getByTestId("death-watch-energy-fill")).toBeVisible();

    await page.screenshot({
      path: "screenshots/T-D-004/01-death-watch-triggered-mobile.png",
      fullPage: true,
    });
  });

  test("renders last-words typewriter + ipfs_degraded tombstone badge", async ({ page }) => {
    await page.addInitScript(() => {
      (window as unknown as { __GENESIS_MOCK_WS__?: unknown[] }).__GENESIS_MOCK_WS__ = [
        {
          kind: "energy_threshold_crossed",
          ts: "2026-05-22T04:00:00Z",
          seq: 1,
          energy_pct: 9.4,
          threshold_pct: 10,
          direction: "below",
        },
        {
          kind: "terminal_lucidity_entered",
          ts: "2026-05-22T04:00:15Z",
          seq: 2,
          breath_at_entry: 9.8,
        },
        {
          kind: "last_words_emitted",
          ts: "2026-05-22T04:30:00Z",
          seq: 3,
          text: "Thank you for watching.",
          tx_hash:
            "0xabc123def4560000000000000000000000000000000000000000000000007890",
        },
        {
          kind: "tombstone_minted",
          ts: "2026-05-22T04:31:00Z",
          seq: 4,
          token_id: "2",
          ipfs_degraded: true,
          tx_hash:
            "0xdeaddeaddeaddeaddeaddeaddeaddeaddeaddeaddeaddeaddeaddeaddeaddead",
        },
      ];
    });
    await page.goto("/");
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("death-watch-root")).toHaveAttribute(
      "data-visible",
      "true",
    );
    await expect(page.getByTestId("death-watch-sticky-badge")).toBeVisible();
    await expect(page.getByTestId("last-words-typewriter")).toBeVisible();
    await expect(
      page.getByTestId("tombstone-mint-animation"),
    ).toHaveAttribute("data-ipfs-degraded", "true");
    await expect(
      page.getByTestId("tombstone-ipfs-degraded-badge"),
    ).toContainText(/memory bank pin failed/i);

    await page.screenshot({
      path: "screenshots/T-D-004/02-death-watch-tombstone-degraded-mobile.png",
      fullPage: true,
    });
  });

  test("stays mounted after terminal lucidity even if breath recovers (PRD §6.10 sticky)", async ({
    page,
  }) => {
    await page.addInitScript(() => {
      (window as unknown as { __GENESIS_MOCK_WS__?: unknown[] }).__GENESIS_MOCK_WS__ = [
        {
          kind: "energy_threshold_crossed",
          ts: "2026-05-22T04:00:00Z",
          seq: 1,
          energy_pct: 9.4,
          threshold_pct: 10,
          direction: "below",
        },
        {
          kind: "terminal_lucidity_entered",
          ts: "2026-05-22T04:00:15Z",
          seq: 2,
          breath_at_entry: 9.8,
        },
      ];
    });
    await page.goto("/");
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("death-watch-root")).toHaveAttribute(
      "data-visible",
      "true",
    );

    // Push a vitals frame with breath > 10 — the sticky latch must hold.
    await page.evaluate(() => {
      (
        window as unknown as { __GENESIS_PUSH_WS__?: (m: unknown) => void }
      ).__GENESIS_PUSH_WS__?.({
        kind: "vitals",
        ts: "2026-05-22T04:05:00Z",
        seq: 10,
        payload: {
          breath: 45,
          bankroll: 90,
          countdown_s: 90,
          gas_per_min: 0.12,
          phase: "PHASE_4_TERMINAL",
        },
      });
    });
    await expect(page.getByTestId("death-watch-root")).toHaveAttribute(
      "data-visible",
      "true",
    );
    await expect(page.getByTestId("death-watch-sticky-badge")).toBeVisible();

    await page.screenshot({
      path: "screenshots/T-D-004/03-death-watch-sticky-mobile.png",
      fullPage: true,
    });
  });
});
