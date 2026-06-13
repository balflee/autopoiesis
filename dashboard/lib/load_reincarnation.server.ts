/**
 * dashboard/lib/load_reincarnation.server.ts — server-only loader for the
 * Phase-2 reincarnation artifacts (gitignored, deploy-only; vercel CLI carries
 * them). Mirrors the survival-journey loader's graceful contract: a MISSING
 * file → `null` (the page degrades to a "pending" note), a present-but-
 * malformed file ALWAYS throws.
 *
 * Path resolution: `<cwd>/public/backtest/<filename>` with per-mode env
 * overrides (`REINCARNATION_PATH` / `REINCARNATION_AI_PATH`).
 */

import { promises as fs } from "node:fs";
import path from "node:path";

import {
  validateReincarnation,
  type ReincarnationFixture,
} from "@/lib/load_reincarnation";

export const REINCARNATION_FILENAME = "reincarnation.json";
export const REINCARNATION_AI_FILENAME = "reincarnation_ai.json";

// A9 emergence-kit arms (MiniMax-M3): G0 kit-off ablation, G1 full kit,
// G2 timestamp-shuffled falsification leg. Deploy-only like the others.
export const REINCARNATION_G0_FILENAME = "reincarnation_g0.json";
export const REINCARNATION_G1_FILENAME = "reincarnation_g1.json";
export const REINCARNATION_G2_FILENAME = "reincarnation_g2.json";

export type ReincarnationMode = "numerical" | "ai" | "g0" | "g1" | "g2";

const MODE_CONFIG: Record<
  ReincarnationMode,
  { readonly filename: string; readonly envVar: string }
> = {
  numerical: { filename: REINCARNATION_FILENAME, envVar: "REINCARNATION_PATH" },
  ai: { filename: REINCARNATION_AI_FILENAME, envVar: "REINCARNATION_AI_PATH" },
  g0: { filename: REINCARNATION_G0_FILENAME, envVar: "REINCARNATION_G0_PATH" },
  g1: { filename: REINCARNATION_G1_FILENAME, envVar: "REINCARNATION_G1_PATH" },
  g2: { filename: REINCARNATION_G2_FILENAME, envVar: "REINCARNATION_G2_PATH" },
};

export function resolveReincarnationPath(
  mode: ReincarnationMode = "numerical",
): string {
  const cfg = MODE_CONFIG[mode];
  const env = process.env[cfg.envVar];
  if (env && env.length > 0) return env;
  return path.join(process.cwd(), "public", "backtest", cfg.filename);
}

function isMissingFileError(err: unknown): boolean {
  return (
    typeof err === "object" &&
    err !== null &&
    "code" in err &&
    (err as { code?: unknown }).code === "ENOENT"
  );
}

/**
 * Read + validate the reincarnation artifact, `null` when the file does not
 * exist (graceful — the run may not have been generated yet). Malformed
 * present files still throw.
 */
export async function loadReincarnationOrNull(opts: {
  readonly mode?: ReincarnationMode;
  readonly filePath?: string;
} = {}): Promise<ReincarnationFixture | null> {
  const filePath = opts.filePath ?? resolveReincarnationPath(opts.mode);
  let raw: string;
  try {
    raw = await fs.readFile(filePath, "utf-8");
  } catch (err) {
    if (isMissingFileError(err)) return null;
    const detail = err instanceof Error ? err.message : String(err);
    throw new Error(
      `loadReincarnation: could not read artifact at '${filePath}': ${detail}`,
    );
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch (err) {
    const detail = err instanceof Error ? err.message : String(err);
    throw new Error(
      `loadReincarnation: invalid JSON at '${filePath}': ${detail}`,
    );
  }
  return validateReincarnation(parsed);
}
