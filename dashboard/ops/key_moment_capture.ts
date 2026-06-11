/*
 * key_moment_capture.ts — T-D-005 sprint_5
 *
 * Listens to Track B's WebSocket as a SECOND observer (alongside the
 * dashboard's own WS client) and fires a full-window screenshot plus
 * a 5-second pre-roll clip on each of the six Demo §9 narrative beats:
 *
 *   1. β₁ activation      — `llm_activated`
 *   2. first BET          — first `decision` with payload.action === "BET"
 *   3. pressure ≥ 0.5     — first `vitals` frame with payload.breath ≤ 50
 *                           (operationalisation: 1 - breath/100 ≥ 0.5)
 *   4. terminal lucidity  — `terminal_lucidity_entered`
 *   5. last words         — `last_words_emitted`
 *   6. tombstone minted   — `tombstone_minted`
 *
 * Per TECHNICAL_PLAN §12 the capture path is observation-only — the
 * subscriber opens its own read-only WS, NEVER calls `socket.send`, and
 * NEVER injects state into the dashboard page. Playwright is used in
 * passive-screenshot mode (no `page.evaluate`, no init scripts).
 *
 * DEDUP DESIGN (acceptance criterion):
 *
 *   The daemon holds a 6-entry Set keyed by NARRATIVE-EVENT-NAME (the
 *   list above). Each fires AT MOST ONCE per run. WS replay on
 *   reconnect, dashboard React re-renders, or duplicate frames from a
 *   polling-fallback overlap all hit the same Set so the screenshot is
 *   never duplicated. The Set is the source of truth — `seq`/timestamp
 *   are not used because the producer may legitimately re-emit the same
 *   `seq` on a stateless backend.
 *
 * Output layout:
 *
 *   <out-dir>/
 *     <iso-ts>_<event-id>.png          full-window screenshot
 *     <iso-ts>_<event-id>.preroll.webm 5-second clip ending at the event
 *     manifest.json                    one line per fired event
 *
 * The pre-roll clip is captured by keeping a rolling 5-second video
 * buffer (Playwright's built-in recording, restarted every 5s) — when
 * a beat lands we close the current context to flush the clip.
 *
 * ─────────────────────────────────────────────────────────────────────
 * Track-D allow-listed path. Node script. Run via:
 *
 *   npx tsx dashboard/ops/key_moment_capture.ts --url http://localhost:3000 \
 *       --ws ws://localhost:8000/ws \
 *       --out dashboard/screenshots/T-D-005/key_moments
 * ─────────────────────────────────────────────────────────────────────
 */

import { appendFileSync, mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

import type { Browser, BrowserContext, Page } from "@playwright/test";

import { isWsMessage, type WsMessage } from "../lib/types";

/* ------------------------------------------------------------------ */
/* Narrative event taxonomy                                            */
/* ------------------------------------------------------------------ */

export const NARRATIVE_EVENT_IDS = [
  "llm_activated",
  "first_bet",
  "pressure_half",
  "terminal_lucidity_entered",
  "last_words_emitted",
  "tombstone_minted",
] as const;

export type NarrativeEventId = (typeof NARRATIVE_EVENT_IDS)[number];

/**
 * Map a WS frame onto a narrative event id IF it matches one of the six
 * beats — null otherwise. Pure function so vitest can exercise it
 * exhaustively without a live socket.
 */
export function classify(msg: WsMessage): NarrativeEventId | null {
  switch (msg.kind) {
    case "llm_activated":
      return "llm_activated";
    case "decision":
      return msg.payload.action === "BET" ? "first_bet" : null;
    case "vitals":
      // Pressure ≥ 0.5 ↔ breath ≤ 50 (with soft-cap = 100 per PRD §4).
      return msg.payload.breath <= 50 ? "pressure_half" : null;
    case "terminal_lucidity_entered":
      return "terminal_lucidity_entered";
    case "last_words_emitted":
      return "last_words_emitted";
    case "tombstone_minted":
      return "tombstone_minted";
    default:
      return null;
  }
}

/* ------------------------------------------------------------------ */
/* Dedup ledger                                                        */
/* ------------------------------------------------------------------ */

export class NarrativeLedger {
  private readonly seen = new Set<NarrativeEventId>();

  /** Returns true iff the event has NOT been fired yet; marks it fired. */
  accept(id: NarrativeEventId): boolean {
    if (this.seen.has(id)) return false;
    this.seen.add(id);
    return true;
  }

  has(id: NarrativeEventId): boolean {
    return this.seen.has(id);
  }

  get firedCount(): number {
    return this.seen.size;
  }

  reset(): void {
    this.seen.clear();
  }
}

/* ------------------------------------------------------------------ */
/* Capture driver                                                     */
/* ------------------------------------------------------------------ */

export interface CaptureArgs {
  readonly url: string;
  readonly wsUrl: string;
  readonly outDir: string;
  readonly width: number;
  readonly height: number;
  readonly maxSeconds: number;
}

export interface FiredEventRecord {
  readonly event_id: NarrativeEventId;
  readonly fired_at: string;
  readonly screenshot_path: string;
  readonly preroll_path: string;
  readonly source_frame: { readonly kind: string; readonly seq: number; readonly ts: string };
}

const DEFAULTS: Omit<CaptureArgs, "url" | "wsUrl" | "outDir"> = {
  width: 1920,
  height: 1080,
  maxSeconds: 360,
};

export function parseArgs(argv: readonly string[]): CaptureArgs {
  let url = "";
  let wsUrl = "";
  let outDir = "";
  let width = DEFAULTS.width;
  let height = DEFAULTS.height;
  let maxSeconds = DEFAULTS.maxSeconds;
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    switch (a) {
      case "--url": url = argv[++i] ?? ""; break;
      case "--ws": wsUrl = argv[++i] ?? ""; break;
      case "--out": outDir = argv[++i] ?? ""; break;
      case "--width": width = Number(argv[++i] ?? DEFAULTS.width); break;
      case "--height": height = Number(argv[++i] ?? DEFAULTS.height); break;
      case "--max-seconds": maxSeconds = Number(argv[++i] ?? DEFAULTS.maxSeconds); break;
      default: break;
    }
  }
  if (!url) throw new Error("key_moment_capture: --url is required");
  if (!wsUrl) throw new Error("key_moment_capture: --ws is required");
  if (!outDir) throw new Error("key_moment_capture: --out is required");
  return { url, wsUrl, outDir, width, height, maxSeconds };
}

function slugiseTs(ts: string): string {
  return ts.replace(/[:.]/g, "-");
}

/**
 * Open the dashboard in a passive Playwright context with rolling 5s
 * video segments. Returns a closure that screenshots the page + flushes
 * the current preroll clip when invoked.
 */
async function bootPassivePage(
  browser: Browser,
  args: CaptureArgs,
): Promise<{
  readonly capture: (eventId: NarrativeEventId, frame: WsMessage) => Promise<{
    readonly screenshotPath: string;
    readonly prerollPath: string;
  }>;
  readonly close: () => Promise<void>;
}> {
  mkdirSync(args.outDir, { recursive: true });

  let context: BrowserContext = await browser.newContext({
    viewport: { width: args.width, height: args.height },
    recordVideo: { dir: args.outDir, size: { width: args.width, height: args.height } },
  });
  let page: Page = await context.newPage();
  await page.goto(args.url, { waitUntil: "domcontentloaded" });

  const rotate = async (): Promise<string | null> => {
    const oldPage = page;
    const oldCtx = context;
    const handle = oldPage.video();
    context = await browser.newContext({
      viewport: { width: args.width, height: args.height },
      recordVideo: { dir: args.outDir, size: { width: args.width, height: args.height } },
    });
    page = await context.newPage();
    await page.goto(args.url, { waitUntil: "domcontentloaded" });
    await oldCtx.close();
    if (handle) {
      try {
        return await handle.path();
      } catch {
        return null;
      }
    }
    return null;
  };

  // Background rotator — every 5s, close the context to flush the
  // pre-roll clip, then immediately open a new one. The "current"
  // clip on `capture()` is the just-flushed one.
  let lastFlushed: string | null = null;
  const rotateTimer = setInterval(() => {
    void rotate().then((p) => {
      if (p) lastFlushed = p;
    });
  }, 5_000);

  return {
    capture: async (eventId, frame) => {
      const slug = slugiseTs(frame.ts);
      const screenshotPath = resolve(args.outDir, `${slug}_${eventId}.png`);
      // Passive screenshot — no DOM mutation.
      await page.screenshot({ path: screenshotPath, fullPage: false });
      // Force-rotate so the just-recorded 5s clip flushes synchronously.
      const flushedPath = await rotate();
      const prerollPath = resolve(args.outDir, `${slug}_${eventId}.preroll.webm`);
      if (flushedPath) {
        try {
          // Rename atomically; on Windows CI fall through if locked.
          const { renameSync } = await import("node:fs");
          renameSync(flushedPath, prerollPath);
          lastFlushed = prerollPath;
        } catch {
          /* leave the auto-named webm in args.outDir */
        }
      }
      return { screenshotPath, prerollPath };
    },
    close: async () => {
      clearInterval(rotateTimer);
      try {
        await context.close();
      } catch {
        /* ignore */
      }
      if (lastFlushed) {
        // Stash reference in case operator wants the final tail clip.
      }
    },
  };
}

/* ------------------------------------------------------------------ */
/* WS observer                                                         */
/* ------------------------------------------------------------------ */

export interface WsObserverHandle {
  readonly stop: () => void;
}

/**
 * Open a read-only WS to the given URL and deliver parsed frames to
 * `onFrame`. The observer NEVER calls `socket.send`. The `WebSocketImpl`
 * seam lets vitest inject a mock-socket without booting playwright.
 */
export function startWsObserver(
  wsUrl: string,
  onFrame: (m: WsMessage) => void,
  WebSocketImpl?: typeof WebSocket,
): WsObserverHandle {
  const Impl = WebSocketImpl ?? (globalThis as { WebSocket?: typeof WebSocket }).WebSocket;
  if (!Impl) {
    throw new Error("key_moment_capture: no WebSocket implementation available");
  }
  const sock = new Impl(wsUrl);
  const handler = (ev: MessageEvent) => {
    let parsed: unknown;
    try {
      parsed = typeof ev.data === "string" ? JSON.parse(ev.data) : ev.data;
    } catch {
      return;
    }
    if (isWsMessage(parsed)) onFrame(parsed);
  };
  sock.addEventListener("message", handler);
  return {
    stop: () => {
      sock.removeEventListener("message", handler);
      try {
        sock.close();
      } catch {
        /* ignore */
      }
    },
  };
}

/* ------------------------------------------------------------------ */
/* Top-level driver                                                   */
/* ------------------------------------------------------------------ */

export async function runCapture(
  browser: Browser,
  args: CaptureArgs,
): Promise<readonly FiredEventRecord[]> {
  const ledger = new NarrativeLedger();
  const manifest: FiredEventRecord[] = [];
  const manifestPath = resolve(args.outDir, "manifest.json");
  writeFileSync(manifestPath, "", "utf8");

  const passive = await bootPassivePage(browser, args);

  const settled = await new Promise<readonly FiredEventRecord[]>((resolveOuter) => {
    const obs = startWsObserver(args.wsUrl, (msg) => {
      const id = classify(msg);
      if (!id) return;
      if (!ledger.accept(id)) return;
      void passive
        .capture(id, msg)
        .then(({ screenshotPath, prerollPath }) => {
          const rec: FiredEventRecord = {
            event_id: id,
            fired_at: new Date().toISOString(),
            screenshot_path: screenshotPath,
            preroll_path: prerollPath,
            source_frame: { kind: msg.kind, seq: msg.seq, ts: msg.ts },
          };
          manifest.push(rec);
          appendFileSync(manifestPath, JSON.stringify(rec) + "\n", "utf8");
          if (ledger.firedCount === NARRATIVE_EVENT_IDS.length) {
            obs.stop();
            resolveOuter(manifest);
          }
        })
        .catch((err) => {
          process.stderr.write(`[key_moment_capture] capture failed: ${String(err)}\n`);
        });
    });

    // Bound the run regardless of whether all 6 fire.
    setTimeout(() => {
      obs.stop();
      resolveOuter(manifest);
    }, args.maxSeconds * 1000);
  });

  await passive.close();
  return settled;
}

export async function run(argv: readonly string[]): Promise<number> {
  const args = parseArgs(argv);
  mkdirSync(args.outDir, { recursive: true });
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { chromium } = await import("@playwright/test");
  const browser = await chromium.launch({ headless: true });
  try {
    const fired = await runCapture(browser, args);
    process.stdout.write(`[key_moment_capture] fired ${fired.length}/${NARRATIVE_EVENT_IDS.length} events\n`);
    return fired.length === NARRATIVE_EVENT_IDS.length ? 0 : 1;
  } finally {
    await browser.close();
  }
}

if (typeof process !== "undefined" && process.argv[1]?.endsWith("key_moment_capture.ts")) {
  run(process.argv.slice(2)).then(
    (code) => process.exit(code),
    (err) => {
      process.stderr.write(`[key_moment_capture] fatal: ${String(err)}\n`);
      process.exit(1);
    },
  );
}

export const __TEST__ = { classify, NarrativeLedger, NARRATIVE_EVENT_IDS };
