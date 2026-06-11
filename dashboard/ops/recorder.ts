/*
 * recorder.ts — T-D-005 sprint_5
 *
 * Headless full-window WebM capture of the Dashboard for the Demo §9
 * 5-minute recording window (TECHNICAL_PLAN §8 D19-D20).
 *
 * Hard invariant per TECHNICAL_PLAN §12:
 *
 *   "保险动作都不能侵害 Permadeath trustless 叙事 —
 *    captures are observation-only."
 *
 * That means this script does NOT:
 *   - call `page.evaluate(...)` against the dashboard
 *   - reach into the WsStore, push frames, or mutate any window state
 *   - reach into React refs / forwarded handles
 *   - inject JS, cookies, headers that influence backend behaviour
 *
 * It DOES:
 *   - launch a fresh chromium context with a 1920x1080 viewport
 *   - record a WebM through Playwright's built-in
 *     `BrowserContext({ recordVideo })` (the recording is a passive
 *     screen capture of what the page renders — Playwright never
 *     re-renders on its own)
 *   - write a `metadata.json` sidecar with start/end timestamps, the
 *     URL captured, the recorder version, and the WebM file path
 *
 * Observation-only invariant is asserted by a vitest spec
 * (`tests/dashboard/PlaybackMode.spec.tsx → recorder source AST`) that
 * grep-checks this file for any forbidden token. If you need any of the
 * forbidden APIs, file a `proposed_spec_change` — DO NOT add them.
 *
 * Forbidden tokens (also enforced in the spec):
 *
 *   - page.evaluate
 *   - addInitScript
 *   - exposeFunction
 *   - exposeBinding
 *   - __GENESIS_PUSH_WS__
 *   - __GENESIS_MOCK_WS__
 *   - useWsStore
 *   - WebSocket
 *   - ingest(
 *
 * ─────────────────────────────────────────────────────────────────────
 * Track-D allow-listed path. Node script. Run via:
 *
 *   npx tsx dashboard/ops/recorder.ts --url http://localhost:3000 \
 *       --duration-s 30 --out dashboard/screenshots/T-D-005/recording
 *
 * Optional flags:
 *   --width 1920   --height 1080      (default)
 *   --headless                         (default true; --no-headless for QA)
 *
 * The script exits 0 on a successful capture, non-zero if Playwright
 * fails to launch (e.g. no chromium installed). It is NOT a hard gate;
 * the orchestrator's playwright_smoke gate runs it with a 30-second
 * window.
 * ─────────────────────────────────────────────────────────────────────
 */

import { mkdirSync, renameSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";

import type { Browser, BrowserContext, Page } from "@playwright/test";

export const RECORDER_VERSION = "0.1.0";

export interface RecorderArgs {
  readonly url: string;
  readonly durationS: number;
  readonly outDir: string;
  readonly width: number;
  readonly height: number;
  readonly headless: boolean;
}

export interface RecorderMetadata {
  readonly recorder_version: string;
  readonly url: string;
  readonly viewport: { readonly width: number; readonly height: number };
  readonly headless: boolean;
  readonly started_at: string;
  readonly ended_at: string;
  readonly duration_s_requested: number;
  readonly duration_s_actual: number;
  readonly video_path: string;
  readonly observation_only: true;
}

const DEFAULTS: Omit<RecorderArgs, "outDir" | "url"> = {
  durationS: 30,
  width: 1920,
  height: 1080,
  headless: true,
};

/** Parse CLI argv (no external deps). */
export function parseArgs(argv: readonly string[]): RecorderArgs {
  let url = "";
  let outDir = "";
  let durationS = DEFAULTS.durationS;
  let width = DEFAULTS.width;
  let height = DEFAULTS.height;
  let headless = DEFAULTS.headless;
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    switch (a) {
      case "--url":
        url = argv[++i] ?? "";
        break;
      case "--duration-s":
        durationS = Number(argv[++i] ?? DEFAULTS.durationS);
        break;
      case "--out":
        outDir = argv[++i] ?? "";
        break;
      case "--width":
        width = Number(argv[++i] ?? DEFAULTS.width);
        break;
      case "--height":
        height = Number(argv[++i] ?? DEFAULTS.height);
        break;
      case "--headless":
        headless = true;
        break;
      case "--no-headless":
        headless = false;
        break;
      default:
        // Ignore unknown flags — keeps the surface narrow for the
        // observation-only AST scan. We do NOT accept any flag that
        // could inject JS into the page (no --eval, --init-script).
        break;
    }
  }
  if (!url) throw new Error("recorder: --url is required");
  if (!outDir) throw new Error("recorder: --out is required");
  if (!Number.isFinite(durationS) || durationS <= 0) {
    throw new Error("recorder: --duration-s must be a positive number");
  }
  return { url, durationS, outDir, width, height, headless };
}

/**
 * Wait passively for `ms` milliseconds. We deliberately use the timer
 * primitive — NOT `page.waitForTimeout(ms)` — because that would still
 * be a passive wait through Playwright, but using setTimeout here makes
 * the observation-only invariant trivially auditable.
 */
function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

/**
 * Drive the capture. Exported so a smoke harness can call it with a
 * pre-built browser instance instead of launching chromium.
 */
export async function captureWith(
  browser: Browser,
  args: RecorderArgs,
): Promise<RecorderMetadata> {
  mkdirSync(args.outDir, { recursive: true });
  const startedAt = new Date();

  // Passive video capture — Playwright records what the page draws.
  // No init script, no evaluate, no exposeFunction.
  const context: BrowserContext = await browser.newContext({
    viewport: { width: args.width, height: args.height },
    recordVideo: {
      dir: args.outDir,
      size: { width: args.width, height: args.height },
    },
  });

  const page: Page = await context.newPage();
  await page.goto(args.url, { waitUntil: "domcontentloaded" });

  // Passively wait. We do NOT poll the DOM for state — that would still
  // be observation-only, but the simpler API also keeps the AST clean.
  await sleep(args.durationS * 1000);

  // Closing the context flushes the WebM to disk.
  const videoHandle = page.video();
  await context.close();

  const endedAt = new Date();

  // The video file lives in args.outDir with a Playwright-assigned UUID
  // basename; rename to a stable filename keyed by `started_at`.
  const stableName = `recording_${startedAt.toISOString().replace(/[:.]/g, "-")}.webm`;
  const stablePath = resolve(args.outDir, stableName);
  if (videoHandle) {
    const tmp = await videoHandle.path();
    if (tmp) {
      try {
        renameSync(tmp, stablePath);
      } catch {
        // Some platforms (Windows in CI) hold the temp file briefly —
        // fall back to leaving the UUID filename in place.
      }
    }
  }

  const metadata: RecorderMetadata = {
    recorder_version: RECORDER_VERSION,
    url: args.url,
    viewport: { width: args.width, height: args.height },
    headless: args.headless,
    started_at: startedAt.toISOString(),
    ended_at: endedAt.toISOString(),
    duration_s_requested: args.durationS,
    duration_s_actual: (endedAt.getTime() - startedAt.getTime()) / 1000,
    video_path: stablePath,
    observation_only: true,
  };

  writeFileSync(
    resolve(args.outDir, "metadata.json"),
    JSON.stringify(metadata, null, 2) + "\n",
    "utf8",
  );
  return metadata;
}

/* ------------------------------------------------------------------ */
/* CLI entrypoint                                                     */
/* ------------------------------------------------------------------ */

export async function run(argv: readonly string[]): Promise<number> {
  const args = parseArgs(argv);
  mkdirSync(dirname(resolve(args.outDir, "metadata.json")), { recursive: true });

  // Dynamic import keeps the AST observation-only — the module is only
  // resolved at runtime when the operator actually runs `recorder.ts`.
  // It also keeps `pnpm build` (Next.js) from trying to bundle
  // @playwright/test into the client manifest.
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { chromium } = await import("@playwright/test");
  const browser = await chromium.launch({ headless: args.headless });
  try {
    const meta = await captureWith(browser, args);
    process.stdout.write(
      `[recorder] captured ${meta.duration_s_actual.toFixed(2)}s → ${meta.video_path}\n`,
    );
    return 0;
  } finally {
    await browser.close();
  }
}

if (typeof process !== "undefined" && process.argv[1]?.endsWith("recorder.ts")) {
  run(process.argv.slice(2)).then(
    (code) => process.exit(code),
    (err) => {
      process.stderr.write(`[recorder] fatal: ${String(err)}\n`);
      process.exit(1);
    },
  );
}
