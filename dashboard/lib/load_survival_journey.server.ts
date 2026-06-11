/**
 * dashboard/lib/load_survival_journey.server.ts — server-only loader.
 *
 * Companion to {@link import("./load_survival_journey")} that owns the
 * `node:fs` read of the large, gitignored survival-journey artifacts
 * (`public/backtest/survival_journey.json` + `survival_journey_ai.json`,
 * ~4 MB each). Split out so the client bundle is not polluted with Node
 * built-ins and the JSON is never inlined into a chunk.
 *
 * TWO journeys, IDENTICAL schema (E-toggle):
 *   - "numerical" → `survival_journey.json`  (deterministic WeightUpdater EMA;
 *     no LLM). Always present in a generated checkout.
 *   - "ai"        → `survival_journey_ai.json` (same engine + real Gemini
 *     reflection + auto-applied strategy proposals; its steps carry populated
 *     `reflection` annotations). May be ABSENT (not generated yet / fresh
 *     checkout) — the loader degrades gracefully to `null` rather than throwing.
 *
 * Path resolution:
 *   - Default is `<cwd>/public/backtest/<filename>`. The `/survival` route is a
 *     server component, so `process.cwd()` is the `dashboard/` directory under
 *     `next build` / `next start`.
 *   - `SURVIVAL_JOURNEY_PATH` / `SURVIVAL_JOURNEY_AI_PATH` override the resolved
 *     path per mode (off-repo deploys / tests).
 *
 * Failure modes:
 *   - A MISSING file → `null` from {@link loadSurvivalJourneyOrNull} (the route
 *     degrades to a "not generated" / "pending" note). The legacy
 *     {@link loadSurvivalJourney} still THROWS on a missing numerical file so a
 *     broken primary artifact fails loudly at build time.
 *   - A MALFORMED file (present but bad JSON / schema drift) ALWAYS throws — a
 *     corrupt fixture should never be silently swallowed.
 */

import { promises as fs } from "node:fs";
import path from "node:path";

import {
  validateSurvivalJourney,
  type SurvivalJourneyFixture,
} from "@/lib/load_survival_journey";

export const SURVIVAL_JOURNEY_FILENAME = "survival_journey.json";
export const SURVIVAL_JOURNEY_AI_FILENAME = "survival_journey_ai.json";
// Archived PRE-REALISM-RULES snapshots (the "finetune process" exhibits): the
// run1 files are the original journeys preserved verbatim when the entry-price
// floor + per-bet PnL cap were introduced. Optional — absent on fresh checkouts.
export const SURVIVAL_JOURNEY_RUN1_FILENAME = "survival_journey_run1.json";
export const SURVIVAL_JOURNEY_AI_RUN1_FILENAME = "survival_journey_ai_run1.json";
// Provider-comparison leg: a Gemini-only full run under the SAME realism rules
// (the v2 AI artifact above was carried by the MiniMax fallback). Optional.
export const SURVIVAL_JOURNEY_AI_GEMINI_FILENAME =
  "survival_journey_ai_gemini.json";

/** The journeys surfaced by the /survival toggle (current + archived + provider legs). */
export type SurvivalJourneyMode =
  | "numerical"
  | "ai"
  | "ai_gemini"
  | "numerical_run1"
  | "ai_run1";

/** Per-mode artifact filename + env-override variable. */
const MODE_CONFIG: Record<
  SurvivalJourneyMode,
  { readonly filename: string; readonly envVar: string }
> = {
  numerical: { filename: SURVIVAL_JOURNEY_FILENAME, envVar: "SURVIVAL_JOURNEY_PATH" },
  ai: { filename: SURVIVAL_JOURNEY_AI_FILENAME, envVar: "SURVIVAL_JOURNEY_AI_PATH" },
  ai_gemini: {
    filename: SURVIVAL_JOURNEY_AI_GEMINI_FILENAME,
    envVar: "SURVIVAL_JOURNEY_AI_GEMINI_PATH",
  },
  numerical_run1: {
    filename: SURVIVAL_JOURNEY_RUN1_FILENAME,
    envVar: "SURVIVAL_JOURNEY_RUN1_PATH",
  },
  ai_run1: {
    filename: SURVIVAL_JOURNEY_AI_RUN1_FILENAME,
    envVar: "SURVIVAL_JOURNEY_AI_RUN1_PATH",
  },
};

/** Resolve the survival-journey artifact path for a mode — env override > default. */
export function resolveSurvivalJourneyPath(
  mode: SurvivalJourneyMode = "numerical",
): string {
  const cfg = MODE_CONFIG[mode];
  const env = process.env[cfg.envVar];
  if (env && env.length > 0) return env;
  return path.join(process.cwd(), "public", "backtest", cfg.filename);
}

export interface SurvivalServerLoaderOptions {
  /** Explicit path override (tests inject a fixture path). */
  readonly filePath?: string;
  /** Which journey to load. Defaults to "numerical". */
  readonly mode?: SurvivalJourneyMode;
}

/** Node's `fs` errors carry a string `code` ("ENOENT" for a missing file). */
function isMissingFileError(err: unknown): boolean {
  return (
    typeof err === "object" &&
    err !== null &&
    "code" in err &&
    (err as { code?: unknown }).code === "ENOENT"
  );
}

/**
 * Read + validate the survival-journey fixture from disk, returning `null` when
 * the file does not exist (the GRACEFUL path the toggle uses for the AI run on a
 * fresh checkout). A present-but-malformed file STILL throws.
 *
 * Throws {@link import("./load_survival_journey").SurvivalJourneyError} on
 * schema drift, or a wrapped error on an unreadable (but existing) file.
 */
export async function loadSurvivalJourneyOrNull(
  opts: SurvivalServerLoaderOptions = {},
): Promise<SurvivalJourneyFixture | null> {
  const filePath = opts.filePath ?? resolveSurvivalJourneyPath(opts.mode);
  let raw: string;
  try {
    raw = await fs.readFile(filePath, "utf-8");
  } catch (err) {
    if (isMissingFileError(err)) return null; // graceful: artifact not generated.
    const detail = err instanceof Error ? err.message : String(err);
    throw new Error(
      `loadSurvivalJourney: could not read survival journey at '${filePath}': ${detail}`,
    );
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch (err) {
    const detail = err instanceof Error ? err.message : String(err);
    throw new Error(`loadSurvivalJourney: invalid JSON at '${filePath}': ${detail}`);
  }
  return validateSurvivalJourney(parsed);
}

/**
 * Read + validate the survival-journey fixture from disk.
 *
 * Back-compat wrapper that THROWS on a missing/unreadable file (the original
 * contract — the numerical artifact is a required build input). For the
 * optional AI journey prefer {@link loadSurvivalJourneyOrNull}.
 *
 * Throws {@link import("./load_survival_journey").SurvivalJourneyError} on
 * schema drift, or a wrapped error on a missing/unreadable file.
 */
export async function loadSurvivalJourney(
  opts: SurvivalServerLoaderOptions = {},
): Promise<SurvivalJourneyFixture> {
  const filePath = opts.filePath ?? resolveSurvivalJourneyPath(opts.mode);
  let raw: string;
  try {
    raw = await fs.readFile(filePath, "utf-8");
  } catch (err) {
    const detail = err instanceof Error ? err.message : String(err);
    throw new Error(
      `loadSurvivalJourney: could not read survival journey at '${filePath}': ${detail}`,
    );
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch (err) {
    const detail = err instanceof Error ? err.message : String(err);
    throw new Error(`loadSurvivalJourney: invalid JSON at '${filePath}': ${detail}`);
  }
  return validateSurvivalJourney(parsed);
}
