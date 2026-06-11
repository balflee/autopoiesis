import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * money_shots.spec.ts — T-D-007 demo artefact capture.
 *
 * Generates the three Money Shot PNGs the brief requires under
 * `dashboard/public/demo/`. Each capture runs against the LIVE dashboard
 * (Playwright drives the production `next start` build) at 1920×1080,
 * never a synthetic mockup. The total bundle is capped at 2 MB.
 *
 *   - money_shot_last_words.png  — Death Watch surface with typewriter
 *                                  mid-pulse + sticky terminal badge.
 *   - money_shot_energy_zero.png — BREATH bar at zero, countdown 00:00,
 *                                  cause-of-death rendered (Phase 4).
 *   - money_shot_tombstone_mint.png — Tombstone NFT mint flourish with
 *                                  token_id + tx_hash receipt visible.
 *
 * The PNGs are committed alongside the test so the demo can ship without
 * needing to rerun Playwright. The test ALSO asserts the file size +
 * dimensions after each capture so the 2 MB total budget cannot silently
 * regress.
 *
 * Path math: Playwright launches with cwd = dashboard/, so all path
 * resolution is relative to the dashboard root — no `import.meta.url`
 * (that would force the TS loader into ESM and break the CommonJS
 * compilation Playwright's bundled transformer uses).
 */

// Playwright runs from the dashboard/ root (webServer config), so plain
// relative paths work — no need for fileURLToPath gymnastics.
const DEMO_DIR = path.resolve("public/demo");
const MAX_TOTAL_BYTES = 2 * 1024 * 1024;
const MIN_W = 1920;
const MIN_H = 1080;

test.beforeAll(() => {
  if (!fs.existsSync(DEMO_DIR)) fs.mkdirSync(DEMO_DIR, { recursive: true });
});

/**
 * The capture suite is desktop-only. We pin viewport to 1920×1080 and
 * deviceScaleFactor=1 so the spec stays deterministic even if
 * playwright.config.ts evolves. The mobile (Pixel 5) project carries
 * a deviceScaleFactor of ~2.6 which would otherwise scale the
 * screenshot to ~5040×2835 px and blow the 2 MB total budget.
 */
test.use({
  viewport: { width: 1920, height: 1080 },
  deviceScaleFactor: 1,
});

test.beforeEach(({ browserName }, testInfo) => {
  // Skip everything on the mobile project — the Demo Money Shot bundle
  // is captured at desktop fidelity once and committed; re-running on
  // the mobile profile would just overwrite the desktop PNGs at a
  // wrong DPR. browserName is shared between projects, so we discriminate
  // by project.name.
  if (testInfo.project.name === "mobile") {
    test.skip(true, "money_shots is captured against desktop only");
  }
});

test.describe("Money Shot capture — Demo climax artefacts", () => {
  test("money_shot_last_words.png — Death Watch + typewriter mid-pulse", async ({
    page,
  }) => {
    await page.addInitScript(() => {
      (window as unknown as { __GENESIS_MOCK_WS__?: unknown[] }).__GENESIS_MOCK_WS__ =
        [
          {
            kind: "vitals",
            ts: "2026-05-22T04:00:00Z",
            seq: 1,
            payload: {
              breath: 6.4,
              bankroll: 142.5,
              countdown_s: 30,
              gas_per_min: 1.5,
              phase: "PHASE_4_TERMINAL",
            },
          },
          {
            kind: "energy_threshold_crossed",
            ts: "2026-05-22T04:00:00Z",
            seq: 2,
            energy_pct: 6.4,
            threshold_pct: 10,
            direction: "below",
          },
          {
            kind: "terminal_lucidity_entered",
            ts: "2026-05-22T04:00:15Z",
            seq: 3,
            breath_at_entry: 8.0,
          },
          {
            kind: "last_words_emitted",
            ts: "2026-05-22T04:00:30Z",
            seq: 4,
            text: "Thank you for watching. Never trust beta-1 alone when alpha-3 is silent.",
            tx_hash:
              "0xabc123def4560000000000000000000000000000000000000000000000007890",
          },
        ];
    });
    await page.goto("/");
    await page.keyboard.press("Escape");

    await expect(page.getByTestId("death-watch-root")).toHaveAttribute(
      "data-visible",
      "true",
    );
    await expect(page.getByTestId("last-words-typewriter")).toBeVisible();
    // Let the typewriter type ~half of the message before the capture so
    // the demo image catches the in-flight motion.
    await page.waitForTimeout(600);

    const outPath = path.join(DEMO_DIR, "money_shot_last_words.png");
    await page.screenshot({ path: outPath, fullPage: false });
    expect(fs.existsSync(outPath)).toBe(true);
  });

  test("money_shot_energy_zero.png — BREATH=0, countdown 00:00, cause_of_death rendered", async ({
    page,
  }) => {
    await page.addInitScript(() => {
      (window as unknown as { __GENESIS_MOCK_WS__?: unknown[] }).__GENESIS_MOCK_WS__ =
        [
          {
            kind: "vitals",
            ts: "2026-05-22T04:00:00Z",
            seq: 1,
            payload: {
              breath: 0,
              bankroll: 142.5,
              countdown_s: 0,
              gas_per_min: 1.2,
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
            kind: "terminal_lucidity_entered",
            ts: "2026-05-22T04:00:00Z",
            seq: 3,
            breath_at_entry: 0,
          },
          {
            kind: "death",
            ts: "2026-05-22T04:00:01Z",
            seq: 4,
            cause: "ENERGY_DEPLETED",
          },
        ];
    });
    await page.goto("/");
    await page.keyboard.press("Escape");

    await expect(page.getByTestId("death-watch-root")).toHaveAttribute(
      "data-visible",
      "true",
    );
    await expect(page.getByTestId("countdown-widget")).toHaveAttribute(
      "data-formatted",
      "00:00",
    );

    const outPath = path.join(DEMO_DIR, "money_shot_energy_zero.png");
    await page.screenshot({ path: outPath, fullPage: false });
    expect(fs.existsSync(outPath)).toBe(true);
  });

  test("money_shot_tombstone_mint.png — TombstoneMintAnimation + receipt visible", async ({
    page,
  }) => {
    await page.addInitScript(() => {
      (window as unknown as { __GENESIS_MOCK_WS__?: unknown[] }).__GENESIS_MOCK_WS__ =
        [
          {
            kind: "vitals",
            ts: "2026-05-22T04:00:00Z",
            seq: 1,
            payload: {
              breath: 0,
              bankroll: 142.5,
              countdown_s: 0,
              gas_per_min: 1.0,
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
            kind: "terminal_lucidity_entered",
            ts: "2026-05-22T04:00:00Z",
            seq: 3,
            breath_at_entry: 0,
          },
          {
            kind: "death",
            ts: "2026-05-22T04:00:01Z",
            seq: 4,
            cause: "TERMINAL_LUCIDITY_COMPLETED",
          },
          {
            kind: "tombstone_minted",
            ts: "2026-05-22T04:01:00Z",
            seq: 5,
            token_id: "1",
            ipfs_cid: "bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi",
            ipfs_degraded: false,
            tx_hash:
              "0xdeaddeaddeaddeaddeaddeaddeaddeaddeaddeaddeaddeaddeaddeaddeaddead",
          },
        ];
    });
    await page.goto("/");
    await page.keyboard.press("Escape");

    await expect(page.getByTestId("tombstone-mint-animation")).toBeVisible();
    // Wait for the rise animation to finish so the capture is clean.
    await page.waitForTimeout(1200);

    const outPath = path.join(DEMO_DIR, "money_shot_tombstone_mint.png");
    await page.screenshot({ path: outPath, fullPage: false });
    expect(fs.existsSync(outPath)).toBe(true);
  });

  test("budget audit — three PNGs exist, each at least 1920x1080, total at most 2 MB", () => {
    const files = [
      "money_shot_last_words.png",
      "money_shot_energy_zero.png",
      "money_shot_tombstone_mint.png",
    ];

    let totalBytes = 0;
    for (const f of files) {
      const p = path.join(DEMO_DIR, f);
      expect(fs.existsSync(p), `${f} must exist`).toBe(true);
      const st = fs.statSync(p);
      totalBytes += st.size;
    }

    expect(totalBytes, "total demo bundle size").toBeLessThanOrEqual(
      MAX_TOTAL_BYTES,
    );

    // Read PNG header (8-byte signature + IHDR) to assert dimensions
    // without a third-party dep. Cheaper than spinning up sharp.
    for (const f of files) {
      const buf = fs.readFileSync(path.join(DEMO_DIR, f));
      // PNG signature 0x89 50 4E 47 0D 0A 1A 0A
      expect(buf[0]).toBe(0x89);
      expect(buf[1]).toBe(0x50);
      // IHDR is the first chunk; width = bytes 16..19, height = 20..23.
      const width = buf.readUInt32BE(16);
      const height = buf.readUInt32BE(20);
      expect(width, `${f} width`).toBeGreaterThanOrEqual(MIN_W);
      expect(height, `${f} height`).toBeGreaterThanOrEqual(MIN_H);
    }

    // Surface the byte total in the playwright log so the orchestrator
    // can paste it into the delivery report verbatim.
    // eslint-disable-next-line no-console
    console.log(`money_shots total bytes: ${totalBytes}`);
  });
});
