import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * /backtest route Playwright e2e spec — D2 (T-D-002) acceptance.
 *
 * The page was rebuilt from the former Phase-1 "training time machine"
 * (scrubber + 6 weight curves + P&L baselines + current-match card) onto
 * the REAL config-sweep result (`lib/load_static_sweep.ts`) in the abyssal
 * design system. This spec replaces the obsolete scrubber assertions with
 * the four static-sweep story panels:
 *
 *   - the optimal SEED config (fusion-weight bars + sizing knobs + the
 *     slot-name repurpose note)
 *   - the methodology story (real signals, 65.7% coverage, $5 cap, ≥50 gate)
 *   - the sortable robust frontier (10 ranked rows; click to re-sort)
 *   - the bet drill-down (real resolved markets with their 5 signal scores)
 *
 * The page is fully static (no WS bootstrap), so the console-error bar is
 * "zero console errors for the duration of the test, period."
 */

const SCREENSHOT_DIR = "screenshots/T-D-002";

test.beforeAll(() => {
  fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
});

test("backtest route loads with zero console errors and all four story panels", async ({
  page,
}, testInfo) => {
  const consoleErrors: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") {
      consoleErrors.push(msg.text());
    }
  });
  page.on("pageerror", (err) => {
    consoleErrors.push(`pageerror: ${err.message}`);
  });

  await page.goto("/backtest");

  await expect(page.getByTestId("backtest-route")).toBeVisible();
  await expect(page.getByTestId("optimal-seed-panel")).toBeVisible();
  await expect(page.getByTestId("methodology-panel")).toBeVisible();
  await expect(page.getByTestId("frontier-table")).toBeVisible();
  await expect(page.getByTestId("bet-drilldown")).toBeVisible();

  // The hero carries the headline Sharpe + the abyss scope class.
  await expect(page.getByRole("heading", { name: "BACKTEST" })).toBeVisible();
  await expect(page.getByTestId("backtest-route")).toHaveClass(/abyss/);

  await page.waitForTimeout(150);
  expect(consoleErrors, `unexpected console errors: ${consoleErrors.join(" | ")}`).toEqual([]);

  const suffix = testInfo.project.name === "mobile" ? "mobile" : "desktop";
  await page.screenshot({
    path: path.join(SCREENSHOT_DIR, `01-overview-${suffix}.png`),
    fullPage: true,
  });
});

test("optimal-seed panel renders a labeled bar for every fusion weight + the slot note", async ({
  page,
}) => {
  await page.goto("/backtest");
  await expect(page.getByTestId("optimal-seed-panel")).toBeVisible();

  for (const key of ["w_r", "w_s", "alpha_1", "alpha_2", "alpha_3", "beta_1", "beta_2", "rho"]) {
    await expect(page.getByTestId(`weight-bar-${key}`)).toBeVisible();
  }
  await expect(page.getByTestId("slot-repurpose-note")).toContainText(/head-to-head/i);
});

test("frontier table renders 10 ranked rows and re-sorts on a column click", async ({ page }) => {
  await page.goto("/backtest");
  const table = page.getByTestId("frontier-table");
  await expect(table).toBeVisible();

  for (let rank = 1; rank <= 10; rank += 1) {
    await expect(page.getByTestId(`frontier-row-${rank}`)).toBeVisible();
  }

  // Default sort is Sharpe-desc → rank 1 (the ★ seed) is the first body row.
  const firstRow = table.locator("tbody tr").first();
  await expect(firstRow).toHaveAttribute("data-testid", "frontier-row-1");

  // Sort by Net P&L (desc) → rank 1 (smallest abs PnL on the frontier) is no
  // longer first.
  await page.getByTestId("frontier-sort-net_pnl").click();
  const afterSortFirst = table.locator("tbody tr").first();
  await expect(afterSortFirst).not.toHaveAttribute("data-testid", "frontier-row-1");
});

test("bet drill-down lists real resolved markets with their five signal scores", async ({
  page,
}, testInfo) => {
  await page.goto("/backtest");
  const drill = page.getByTestId("bet-drilldown");
  await expect(drill).toBeVisible();

  const rows = drill.locator('[data-testid^="bet-row-"]');
  await expect(rows.first()).toBeVisible();
  expect(await rows.count()).toBeGreaterThanOrEqual(8);

  // The known big NO winner over Erjavec/Rybakina (market 2329921) shows a
  // positive realised P&L in the glow color.
  const winner = page.getByTestId("bet-row-2329921");
  await expect(winner).toContainText(/Rybakina/);
  await expect(page.getByTestId("bet-pnl-2329921")).toContainText(/\$45/);

  const suffix = testInfo.project.name === "mobile" ? "mobile" : "desktop";
  await page.screenshot({
    path: path.join(SCREENSHOT_DIR, `02-drilldown-${suffix}.png`),
    fullPage: true,
  });
});

test("the page links back to the roadmap lifeline and forward to /survival", async ({ page }) => {
  await page.goto("/backtest");
  await expect(page.getByTestId("backtest-back-link")).toHaveAttribute("href", "/roadmap");
  await expect(page.locator('a[href="/survival"]')).toBeVisible();
});
