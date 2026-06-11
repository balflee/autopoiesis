import { expect, test } from "@playwright/test";
import child_process from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const { spawn } = child_process;
const { existsSync, mkdirSync, readFileSync } = fs;
const { resolve } = path;

/**
 * tests/playwright/lighthouse.spec.ts — T-D-007 Lighthouse gate.
 *
 * Loads `dashboard/lighthouserc.json` for the thresholds + URL, shells
 * out to `npx lighthouse` (same path the existing scripts/lighthouse.mjs
 * uses — keeps the dev/CI behaviour aligned), parses the report, and
 * asserts the four category scores against the thresholds block.
 *
 * Acceptance thresholds (T-D-007):
 *   - performance     ≥ 85
 *   - accessibility   ≥ 90
 *   - best-practices  ≥ 85
 *   - SEO             ≥ 85
 *
 * The spec runs as part of the Playwright project so it shares the
 * webServer (next start on :3100) with death_watch.spec.ts — no extra
 * boot cost. The lighthouse_perf precondition `lighthouse_target_set`
 * is met by the presence of `dashboard/lighthouserc.json`.
 *
 * If lighthouse is unavailable (e.g. offline CI image without npm
 * cache), the spec skips with a clear marker rather than failing —
 * orchestrator infra owns making sure the binary is reachable.
 */

interface LighthouseConfig {
  readonly url: string;
  readonly preset: string;
  readonly thresholds: {
    readonly performance: number;
    readonly accessibility: number;
    readonly best_practices: number;
    readonly seo: number;
  };
  readonly chrome_flags: readonly string[];
}

/**
 * Playwright runs with cwd = dashboard/, so the lighthouserc.json lives
 * one directory up from the spec (resolve("lighthouserc.json")). Avoid
 * `import.meta.url` because Playwright's TS transformer compiles it as
 * CommonJS for non-ESM `.spec.ts` files, and using `import.meta` flips
 * the loader to ESM mid-compile → "require is not defined" at runtime.
 */
const RC_PATH = resolve("lighthouserc.json");
const OUT_DIR = resolve("screenshots/T-D-007");
const REPORT_PATH = resolve(OUT_DIR, "lighthouse.json");

function loadConfig(): LighthouseConfig {
  const raw = readFileSync(RC_PATH, "utf8");
  return JSON.parse(raw) as LighthouseConfig;
}

function runLighthouse(cfg: LighthouseConfig): Promise<{
  readonly exit_code: number;
  readonly stdout: string;
  readonly stderr: string;
}> {
  return new Promise((resolvePromise, rejectPromise) => {
    if (!existsSync(OUT_DIR)) mkdirSync(OUT_DIR, { recursive: true });
    // Run via the project's existing lighthouse runner. Reusing the
    // node script (rather than re-implementing the spawn here) avoids
    // the Windows shell-escape sharpness — the script handles `npx`
    // invocation in exactly the same way the manual `npm run lighthouse`
    // path the dev team already trusts does.
    const env = {
      ...process.env,
      LIGHTHOUSE_URL: cfg.url,
      LIGHTHOUSE_PERF_MIN: String(cfg.thresholds.performance),
      LIGHTHOUSE_A11Y_MIN: String(cfg.thresholds.accessibility),
      LIGHTHOUSE_OUT: REPORT_PATH,
      CI: "1",
      FORCE_COLOR: "0",
    };
    // Use Node directly to drive the script — no shell, no PATH games.
    const child = spawn(process.execPath, ["scripts/lighthouse.mjs"], {
      stdio: ["ignore", "pipe", "pipe"],
      env,
    });
    let stdout = "";
    let stderr = "";
    child.stdout?.on("data", (d: Buffer) => {
      stdout += d.toString();
    });
    child.stderr?.on("data", (d: Buffer) => {
      stderr += d.toString();
    });
    child.on("error", rejectPromise);
    child.on("close", (code) =>
      resolvePromise({ exit_code: code ?? 1, stdout, stderr }),
    );
  });
}

test.describe("Lighthouse — performance/a11y/best-practices/seo gate", () => {
  // Lighthouse is slow (~30 s warm); reserve a generous timeout. The
  // outer playwright.config.ts uses 30 s — bump per-test here.
  test.setTimeout(180_000);

  test("dashboard root meets all four T-D-007 thresholds", async () => {
    const cfg = loadConfig();

    let runResult: {
      readonly exit_code: number;
      readonly stdout: string;
      readonly stderr: string;
    };
    try {
      runResult = await runLighthouse(cfg);
    } catch (err) {
      test.skip(
        true,
        `lighthouse binary unavailable (${(err as Error).message}) — orchestrator infra must install @lhci/cli or lighthouse`,
      );
      return;
    }
    // If lighthouse exited non-zero OR did not write the JSON report
    // (it sometimes returns 0 but writes nothing when chrome flags
    // mis-parse), surface the captured streams in the failure message
    // so the orchestrator's playwright_smoke log carries the root cause.
    if (runResult.exit_code !== 0 || !existsSync(REPORT_PATH)) {
      const msg = `lighthouse failed (exit=${runResult.exit_code}, report_exists=${existsSync(REPORT_PATH)})\nstdout:\n${runResult.stdout}\nstderr:\n${runResult.stderr}`;
      // Skip rather than fail when the binary is reachable but the
      // browser-launch path is missing the Playwright-installed Chromium
      // (common in containers without a system Chrome). The orchestrator
      // owns making sure the binary AND Chrome are reachable in CI.
      if (/chrome.*not.*found|ENOENT|launch.*chrome/i.test(runResult.stderr)) {
        test.skip(true, msg);
        return;
      }
      throw new Error(msg);
    }

    // Read + parse the report. Categories live under `categories.<id>.score`
    // on a 0..1 scale; the gate expects integer 0..100.
    const report = JSON.parse(readFileSync(REPORT_PATH, "utf8")) as {
      readonly categories?: Record<string, { readonly score: number | null }>;
    };
    const categories = report.categories ?? {};
    const pct = (name: string): number =>
      Math.round((categories[name]?.score ?? 0) * 100);

    const perf = pct("performance");
    const a11y = pct("accessibility");
    const bp = pct("best-practices");
    const seo = pct("seo");

    // Print a compact summary so the orchestrator log captures the
    // numerical scores for the delivery_report.
    console.log(
      `lighthouse scores — performance=${perf} accessibility=${a11y} best-practices=${bp} seo=${seo}`,
    );

    expect(perf, "performance score").toBeGreaterThanOrEqual(
      cfg.thresholds.performance,
    );
    expect(a11y, "accessibility score").toBeGreaterThanOrEqual(
      cfg.thresholds.accessibility,
    );
    expect(bp, "best-practices score").toBeGreaterThanOrEqual(
      cfg.thresholds.best_practices,
    );
    expect(seo, "seo score").toBeGreaterThanOrEqual(cfg.thresholds.seo);
  });
});
