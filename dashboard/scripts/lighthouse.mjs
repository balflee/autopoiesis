#!/usr/bin/env node
/**
 * Lighthouse perf + a11y + best-practices + SEO gate for the Dashboard.
 *
 * Usage (dev):
 *   1. Start the production build:   `npm run start -- -p 3100`
 *   2. In another shell:             `npm run lighthouse`
 *
 * Usage (Playwright):
 *   The tests/playwright/lighthouse.spec.ts spec shells out to this
 *   script via `process.execPath` to side-step Windows `npx` shell-quote
 *   sharpness. Threshold env vars are passed through (LIGHTHOUSE_PERF_MIN,
 *   LIGHTHOUSE_A11Y_MIN, LIGHTHOUSE_BP_MIN, LIGHTHOUSE_SEO_MIN). The
 *   default thresholds match `dashboard/lighthouserc.json` so dev runs
 *   pin to the same bar the orchestrator's lighthouse_perf gate uses.
 *
 * Exits non-zero on miss so CI / the orchestrator's lighthouse_perf
 * gate trips. Output is written to LIGHTHOUSE_OUT (defaults to
 * ./screenshots/lighthouse.json).
 *
 * Why a shellout instead of programmatic lighthouse: keeps our deps
 * small (no chrome-launcher, lighthouse, puppeteer) and lets CI image
 * choose its own Chromium. The orchestrator is free to substitute its
 * own runner — wiring through env vars + a stable JSON contract keeps
 * the Playwright spec, the npm script, and the CI image aligned.
 */

import { spawn } from "node:child_process";
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { dirname, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, "..");
// Lighthouse on Windows refuses absolute paths whose enclosing directory
// contains spaces (the CLI argument parser splits on the unquoted space
// and reports "cannot be written to"). Working around that with `cwd: ROOT`
// + a relative output path side-steps the issue without needing shell
// quoting heuristics. See INC-2026-05-24-LIGHTHOUSE-WIN-SPACES.
const DEFAULT_OUT = resolve(ROOT, "screenshots/lighthouse.json");
const OUT = process.env.LIGHTHOUSE_OUT
  ? resolve(process.env.LIGHTHOUSE_OUT)
  : DEFAULT_OUT;

const URL = process.env.LIGHTHOUSE_URL ?? "http://127.0.0.1:3100";
const PERF_MIN = Number(process.env.LIGHTHOUSE_PERF_MIN ?? 85);
const A11Y_MIN = Number(process.env.LIGHTHOUSE_A11Y_MIN ?? 90);
const BP_MIN = Number(process.env.LIGHTHOUSE_BP_MIN ?? 85);
const SEO_MIN = Number(process.env.LIGHTHOUSE_SEO_MIN ?? 85);

const OUT_DIR = dirname(OUT);
if (!existsSync(OUT_DIR)) mkdirSync(OUT_DIR, { recursive: true });

function run() {
  return new Promise((resolvePromise, rejectPromise) => {
    // Pass output as a path relative to the dashboard root + spawn cwd
    // = ROOT so lighthouse never sees a path with spaces. Prepend `./`
    // so lighthouse's argv parser treats it as a path, not a flag.
    const relOut = `./${relative(ROOT, OUT).replaceAll("\\", "/")}`;
    const args = [
      "--yes",
      "lighthouse",
      URL,
      "--output=json",
      `--output-path=${relOut}`,
      "--quiet",
      "--chrome-flags=--headless=new --no-sandbox",
      "--preset=desktop",
      "--only-categories=performance,accessibility,best-practices,seo",
    ];
    const child = spawn("npx", args, {
      stdio: "inherit",
      shell: process.platform === "win32",
      cwd: ROOT,
    });
    child.on("error", rejectPromise);
    child.on("close", (code) => {
      if (code === 0) resolvePromise(undefined);
      else rejectPromise(new Error(`lighthouse exited with code ${code}`));
    });
  });
}

try {
  await run();
  const report = JSON.parse(readFileSync(OUT, "utf8"));
  const score = (k) =>
    Math.round((report?.categories?.[k]?.score ?? 0) * 100);
  const perf = score("performance");
  const a11y = score("accessibility");
  const bp = score("best-practices");
  const seo = score("seo");
  console.log(
    `Lighthouse perf=${perf} a11y=${a11y} best-practices=${bp} seo=${seo}`,
  );
  let failed = false;
  if (perf < PERF_MIN) {
    console.error(`FAIL: performance ${perf} < ${PERF_MIN}`);
    failed = true;
  }
  if (a11y < A11Y_MIN) {
    console.error(`FAIL: accessibility ${a11y} < ${A11Y_MIN}`);
    failed = true;
  }
  if (bp < BP_MIN) {
    console.error(`FAIL: best-practices ${bp} < ${BP_MIN}`);
    failed = true;
  }
  if (seo < SEO_MIN) {
    console.error(`FAIL: seo ${seo} < ${SEO_MIN}`);
    failed = true;
  }
  process.exit(failed ? 1 : 0);
} catch (err) {
  console.error("lighthouse runner failed:", err);
  process.exit(1);
}
