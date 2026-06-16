/**
 * load_survival_journey_server.test.ts — E-toggle server-loader gate.
 *
 * Covers the GRACEFUL multi-mode loader the Numerical/AI survival toggle relies on:
 *   - a present, well-formed file → validated fixture (numerical & ai modes);
 *   - a MISSING file → `null` from `loadSurvivalJourneyOrNull` (the AI-absent
 *     path the toggle degrades into) rather than a throw;
 *   - a present-but-malformed file → STILL throws (corrupt artifact must not be
 *     silently swallowed);
 *   - per-mode path resolution (numerical vs ai filename).
 *
 * Uses `node:os.tmpdir()` for isolation; teardown removes the dir.
 */

import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { SurvivalJourneyError } from "@/lib/load_survival_journey";
import {
  loadSurvivalJourney,
  loadSurvivalJourneyOrNull,
  resolveSurvivalJourneyPath,
  SURVIVAL_JOURNEY_AI_FILENAME,
  SURVIVAL_JOURNEY_FILENAME,
} from "@/lib/load_survival_journey.server";

/* ------------------------------------------------------------------ */
/* Minimal well-formed fixture (mirrors the loader unit test)          */
/* ------------------------------------------------------------------ */

function weights(): Record<string, number> {
  return {
    w_r: 0.5,
    w_s: 0.5,
    alpha_0: 0.4,
    alpha_1: 0.35,
    alpha_2: 0.25,
    beta_0: 0.45,
    beta_1: 0.55,
    rho: 0.2,
  };
}
function signals(): Record<string, number> {
  return {
    tennis_technical: 0.5,
    market_momentum: 0.1,
    surface_advantage: -0.2,
    head_to_head: 1,
    rest_recency: 0,
  };
}
function market(id: string): Record<string, unknown> {
  return {
    entry_price: 0.6,
    market_id: id,
    outcome: "yes",
    players: ["alpha", "bravo"],
    slug: `wta-${id}`,
    surface: "Hard",
  };
}

/** A 1-life / 1-step fixture; optionally stamp `reflection` on the step. */
function rawJourney(withReflection: boolean): Record<string, unknown> {
  const step: Record<string, unknown> = {
    life_idx: 0,
    market: market("m1"),
    side: "YES",
    size: 4,
    pnl: 2,
    cum_pnl: 2,
    breath: 30,
    win_rate: 1,
    weights: weights(),
    weights_before: weights(),
    signals: signals(),
  };
  if (withReflection) {
    step.reflection = "reflected (tick_interval) #1 -> proposed 1 proposal (pending approval)";
  }
  return {
    seed: { max_breath_risk_pct: 0.9, min_bet_size_usd: 4, min_confidence: 0.05, weights: weights() },
    summary: {
      best_life: 0,
      deaths: 0,
      learner_final_pnl: 2,
      learning_vs_static_delta: 2,
      lives: 1,
      static_final_pnl: 0,
      total_steps: 1,
    },
    lives: [
      {
        idx: 0,
        bets: 1,
        pnl: 2,
        death: null,
        final_bankroll_usd: 102,
        final_breath: 30,
        settlements: 1,
        start_ts: "2024-09-01T00:00:00+00:00",
      },
    ],
    steps: [step],
    baselines: {
      static: [{ idx: 0, cum_pnl: 0, pnl: 0, is_bet: false, market_id: "m1", side: null, size: 0 }],
      random: [{ idx: 0, cum_pnl: 0, pnl: 0, is_bet: false, market_id: "m1", side: null, size: 0 }],
      always_favorite: [{ idx: 0, cum_pnl: 0, pnl: 0, is_bet: false, market_id: "m1", side: null, size: 0 }],
    },
  };
}

describe("loadSurvivalJourneyOrNull (E-toggle graceful loader)", () => {
  let root: string;

  beforeEach(async () => {
    root = await fs.mkdtemp(path.join(os.tmpdir(), "survival-journey-"));
  });
  afterEach(async () => {
    try {
      await fs.rm(root, { recursive: true, force: true });
    } catch {
      /* best-effort */
    }
  });

  it("returns null when the file is MISSING (the AI-absent toggle path)", async () => {
    const missing = path.join(root, "does-not-exist.json");
    const result = await loadSurvivalJourneyOrNull({ filePath: missing });
    expect(result).toBeNull();
  });

  it("returns a validated fixture when the file is present + well-formed", async () => {
    const p = path.join(root, "ok.json");
    await fs.writeFile(p, JSON.stringify(rawJourney(false)));
    const result = await loadSurvivalJourneyOrNull({ filePath: p });
    expect(result).not.toBeNull();
    expect(result!.summary.total_steps).toBe(1);
    expect(result!.steps[0]!.reflection).toBeUndefined();
  });

  it("carries the optional `reflection` annotation when present (AI run)", async () => {
    const p = path.join(root, "ai.json");
    await fs.writeFile(p, JSON.stringify(rawJourney(true)));
    const result = await loadSurvivalJourneyOrNull({ filePath: p });
    expect(result!.steps[0]!.reflection).toMatch(/^reflected \(tick_interval\)/);
  });

  it("STILL throws on a present-but-malformed file (no silent swallow)", async () => {
    const p = path.join(root, "bad.json");
    await fs.writeFile(p, "{ not valid json");
    await expect(loadSurvivalJourneyOrNull({ filePath: p })).rejects.toThrow(/invalid JSON/);
  });

  it("STILL throws on a present file that fails schema validation", async () => {
    const p = path.join(root, "drift.json");
    const bad = rawJourney(false);
    // steps.length > total_steps is impossible under the downsample contract
    // (serialized steps can never outnumber the full run).
    (bad.summary as Record<string, unknown>).total_steps = 0;
    await fs.writeFile(p, JSON.stringify(bad));
    await expect(loadSurvivalJourneyOrNull({ filePath: p })).rejects.toBeInstanceOf(
      SurvivalJourneyError,
    );
  });

  it("accepts a DOWNSAMPLED artifact (steps.length < total_steps)", async () => {
    const p = path.join(root, "downsampled.json");
    const sampled = rawJourney(false);
    // 1 serialized step out of a 99-step full run — the normal shape for any
    // run over the exporter's max_steps chart budget.
    (sampled.summary as Record<string, unknown>).total_steps = 99;
    await fs.writeFile(p, JSON.stringify(sampled));
    const result = await loadSurvivalJourneyOrNull({ filePath: p });
    expect(result).not.toBeNull();
    expect(result!.steps).toHaveLength(1);
    expect(result!.summary.total_steps).toBe(99);
  });
});

describe("loadSurvivalJourney (legacy throwing loader)", () => {
  it("throws on a missing file (back-compat — numerical is a required artifact)", async () => {
    await expect(
      loadSurvivalJourney({ filePath: path.join(os.tmpdir(), "nope-survival.json") }),
    ).rejects.toThrow(/could not read survival journey/);
  });
});

describe("resolveSurvivalJourneyPath (per-mode filename)", () => {
  it("defaults to the numerical artifact filename", () => {
    expect(resolveSurvivalJourneyPath()).toContain(SURVIVAL_JOURNEY_FILENAME);
    expect(resolveSurvivalJourneyPath("numerical")).toContain(SURVIVAL_JOURNEY_FILENAME);
  });

  it("resolves the AI artifact filename for mode=ai", () => {
    expect(resolveSurvivalJourneyPath("ai")).toContain(SURVIVAL_JOURNEY_AI_FILENAME);
  });
});
