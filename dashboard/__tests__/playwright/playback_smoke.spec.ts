import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * playback_smoke.spec.ts — T-D-005 sprint_5.
 *
 * Three smoke assertions for the demo capture pipeline:
 *
 *   1. The PlaybackMode toggle is present on the dashboard, defaulting
 *      to LIVE (banner not in DOM).
 *   2. Clicking the toggle engages PlaybackMode, surfaces the
 *      "Demo Playback" banner, and the banner sits at z-[9999].
 *   3. The committed fixture file is loadable from the public path —
 *      consumed by both PlaybackMode (real path) and the operator's
 *      pre-recorded backup (TECHNICAL_PLAN §12).
 *
 * Screenshots are written into dashboard/screenshots/T-D-005/ so the
 * delivery report can reference them.
 */

const T_D_005_SCREENSHOT_DIR = "screenshots/T-D-005";

test.beforeAll(() => {
  fs.mkdirSync(T_D_005_SCREENSHOT_DIR, { recursive: true });
});

test("PlaybackMode toggle defaults to LIVE; banner absent", async ({ page }) => {
  await page.goto("/");
  // The toggle button exists.
  const toggle = page.getByTestId("demo-playback-toggle");
  await expect(toggle).toBeVisible();
  await expect(toggle).toHaveAttribute("aria-pressed", "false");
  // Banner is NOT mounted.
  await expect(page.getByTestId("demo-playback-banner")).toHaveCount(0);
  await page.screenshot({
    path: path.join(T_D_005_SCREENSHOT_DIR, "live_no_banner.png"),
    fullPage: false,
  });
});

test("Engaging PlaybackMode surfaces the persistent banner at z-[9999]", async ({ page }) => {
  await page.goto("/");
  const toggle = page.getByTestId("demo-playback-toggle");
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-pressed", "true");

  const banner = page.getByTestId("demo-playback-banner");
  await expect(banner).toBeVisible();
  await expect(banner).toContainText(/demo playback/i);

  // z-index assertion — read the resolved CSS.
  const z = await banner.evaluate((el) => window.getComputedStyle(el).zIndex);
  expect(Number(z)).toBeGreaterThanOrEqual(9999);

  await page.screenshot({
    path: path.join(T_D_005_SCREENSHOT_DIR, "playback_banner_engaged.png"),
    fullPage: false,
  });
});

test("Fixture file is served from the public path", async ({ request }) => {
  const res = await request.get("/playback_fixtures/golden_scenario_5min.jsonl");
  expect(res.status()).toBe(200);
  const body = await res.text();
  const lines = body.split(/\r?\n/).filter((l) => l.trim().length > 0);
  // 5-minute scenario per build_playback_fixture.ts has 29 frames.
  expect(lines.length).toBeGreaterThanOrEqual(20);
  // Sanity-check: the six narrative kinds are present.
  const kinds = new Set(lines.map((l) => JSON.parse(l).kind));
  for (const required of [
    "llm_activated",
    "decision",
    "vitals",
    "terminal_lucidity_entered",
    "last_words_emitted",
    "tombstone_minted",
  ]) {
    expect(kinds.has(required)).toBe(true);
  }
});
