/**
 * load_survival_journey.test.ts — E1 unit gate (codex M5 adapter).
 *
 * Covers:
 *   - validateSurvivalJourney  — happy path on a hand-built fixture + the
 *     rejection paths (bad summary, missing sections, bad enum/fraction, the
 *     steps.length <= total_steps cross-check (serialized steps are a
 *     DOWNSAMPLE of the full run; total_steps keeps the full count),
 *     `last_tick` death mapping).
 *   - the ADAPTER (adaptWeightViewModel / adaptPnlViewModel /
 *     adaptScrubberViewModel) — that survival steps/lives map cleanly onto the
 *     generic chart view-models WITHOUT being cast into TrainingJourneyFixture.
 *   - the REAL on-disk artifact (public/backtest/survival_journey.json), when
 *     present, validates + adapts. (Gitignored, so guarded by existsSync.)
 *
 * Pure functions, no DOM.
 */

import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import {
  adaptPnlViewModel,
  adaptScrubberViewModel,
  adaptWeightViewModel,
  bestLife,
  deathCount,
  stepAt,
  survivingLife,
  tombstones,
  vitalsForStep,
  SURVIVAL_BASELINE_KEYS,
  SURVIVAL_SIGNAL_KEYS,
  SURVIVAL_WEIGHT_KEYS,
  SurvivalJourneyError,
  validateSurvivalJourney,
  type SurvivalJourneyFixture,
} from "@/lib/load_survival_journey";

/* ------------------------------------------------------------------ */
/* Fixture builders                                                    */
/* ------------------------------------------------------------------ */

function weights(): Record<string, number> {
  return {
    w_r: 0.564,
    w_s: 0.436,
    alpha_0: 0.486,
    alpha_1: 0.328,
    alpha_2: 0.186,
    beta_0: 0.443,
    beta_1: 0.557,
    rho: 0.186,
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
    entry_price: 0.62,
    market_id: id,
    outcome: "yes",
    players: ["alpha", "bravo"],
    slug: `wta-${id}`,
    surface: "Hard",
  };
}

/** A minimal, well-formed survival fixture: 2 lives, 3 steps, 3 baselines. */
function buildRaw(): Record<string, unknown> {
  return {
    seed: {
      max_breath_risk_pct: 0.95,
      min_bet_size_usd: 4,
      min_confidence: 0.049,
      weights: weights(),
    },
    summary: {
      best_life: 1,
      deaths: 1,
      learner_final_pnl: 30,
      learning_vs_static_delta: 25,
      lives: 2,
      static_final_pnl: 5,
      total_steps: 3,
    },
    lives: [
      {
        idx: 0,
        bets: 2,
        pnl: -8,
        death: {
          breath: 0,
          cause: "breath_depleted",
          last_tick: 17,
          kill_tx_hash: "0x00",
          tombstone_token_id: "0",
        },
        final_bankroll_usd: 92,
        final_breath: 0,
        settlements: 2,
        start_ts: "2024-08-30T05:30:02+00:00",
      },
      {
        idx: 1,
        bets: 1,
        pnl: 38,
        death: null,
        final_bankroll_usd: 138,
        final_breath: 1200,
        settlements: 1,
        start_ts: "2024-09-01T00:00:00+00:00",
      },
    ],
    steps: [
      {
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
      },
      {
        life_idx: 0,
        market: market("m2"),
        side: "NO",
        size: 5,
        pnl: -5,
        cum_pnl: -3,
        breath: 10,
        win_rate: 0.5,
        weights: weights(),
        weights_before: weights(),
        signals: signals(),
      },
      {
        life_idx: 1,
        market: market("m3"),
        side: "YES",
        size: 5,
        pnl: 33,
        cum_pnl: 30,
        breath: 1200,
        win_rate: 0.67,
        weights: weights(),
        weights_before: weights(),
        signals: signals(),
      },
    ],
    baselines: {
      static: [
        { idx: 0, cum_pnl: 0, pnl: 0, is_bet: false, market_id: "m1", side: null, size: 0 },
        { idx: 1, cum_pnl: 5, pnl: 5, is_bet: true, market_id: "m2", side: "YES", size: 5 },
      ],
      random: [
        { idx: 0, cum_pnl: -5, pnl: -5, is_bet: true, market_id: "m1", side: "NO", size: 5 },
        { idx: 1, cum_pnl: -5, pnl: 0, is_bet: false, market_id: "m2", side: null, size: 0 },
      ],
      always_favorite: [
        { idx: 0, cum_pnl: 4, pnl: 4, is_bet: true, market_id: "m1", side: "YES", size: 5 },
        { idx: 1, cum_pnl: 9, pnl: 5, is_bet: true, market_id: "m2", side: "YES", size: 5 },
      ],
    },
  };
}

function clone(raw: Record<string, unknown>): Record<string, unknown> {
  return JSON.parse(JSON.stringify(raw)) as Record<string, unknown>;
}

/* ------------------------------------------------------------------ */
/* validateSurvivalJourney — happy path                                */
/* ------------------------------------------------------------------ */

describe("validateSurvivalJourney (happy path)", () => {
  it("validates a well-formed fixture and returns the typed value", () => {
    const f = validateSurvivalJourney(buildRaw());
    expect(f.summary.lives).toBe(2);
    expect(f.summary.total_steps).toBe(3);
    expect(f.lives).toHaveLength(2);
    expect(f.steps).toHaveLength(3);
  });

  it("maps the death `last_tick` JSON key onto `tick`", () => {
    const f = validateSurvivalJourney(buildRaw());
    expect(f.lives[0]!.death).not.toBeNull();
    expect(f.lives[0]!.death!.tick).toBe(17);
    expect(f.lives[0]!.death!.cause).toBe("breath_depleted");
    // The surviving life has no death.
    expect(f.lives[1]!.death).toBeNull();
  });

  it("carries all 8 weight keys + 5 signal keys per step", () => {
    const f = validateSurvivalJourney(buildRaw());
    const step = f.steps[0]!;
    for (const k of SURVIVAL_WEIGHT_KEYS) {
      expect(typeof step.weights[k]).toBe("number");
      expect(typeof step.weights_before[k]).toBe("number");
    }
    for (const k of SURVIVAL_SIGNAL_KEYS) {
      expect(typeof step.signals[k]).toBe("number");
    }
  });

  it("validates all three baselines", () => {
    const f = validateSurvivalJourney(buildRaw());
    for (const base of SURVIVAL_BASELINE_KEYS) {
      expect(f.baselines[base].length).toBeGreaterThan(0);
    }
  });

  it("upgrades a legacy old-key journey via the slot-alias shim", () => {
    // Simulate a non-regenerable archived journey carrying the pre-2026-06-16
    // slot keys; the shim must normalize them so the strict loader validates
    // and the result exposes the NEW keys.
    const raw = clone(buildRaw());
    for (const step of raw.steps as Array<Record<string, unknown>>) {
      step.signals = {
        tennis_technical: 0.5,
        market_momentum: 0.1,
        smart_money: -0.2,
        sentiment_llm: 1,
        crowd_volume: 0,
      };
    }
    const f = validateSurvivalJourney(raw);
    const sig = f.steps[0]!.signals;
    expect(sig.surface_advantage).toBe(-0.2);
    expect(sig.head_to_head).toBe(1);
    expect(sig.rest_recency).toBe(0);
    // Old keys are gone from the validated shape.
    expect((sig as Record<string, unknown>).smart_money).toBeUndefined();
  });

  it("omits the optional `reflection` field on numerical steps (key absent)", () => {
    const f = validateSurvivalJourney(buildRaw());
    for (const step of f.steps) {
      expect(step.reflection).toBeUndefined();
    }
  });

  it("carries the optional `reflection` annotation when an AI step has one", () => {
    const r = clone(buildRaw());
    const note = "reflected (tick_interval) #7 -> proposed 1 proposal (pending approval)";
    ((r.steps as Record<string, unknown>[])[0] as Record<string, unknown>).reflection = note;
    const f = validateSurvivalJourney(r);
    expect(f.steps[0]!.reflection).toBe(note);
    // Steps without the key stay undefined.
    expect(f.steps[1]!.reflection).toBeUndefined();
  });

  it("treats an explicit null `reflection` as absent (numerical byte-shape)", () => {
    const r = clone(buildRaw());
    ((r.steps as Record<string, unknown>[])[0] as Record<string, unknown>).reflection = null;
    const f = validateSurvivalJourney(r);
    expect(f.steps[0]!.reflection).toBeUndefined();
  });

  it("rejects a non-string `reflection` annotation", () => {
    const r = clone(buildRaw());
    ((r.steps as Record<string, unknown>[])[0] as Record<string, unknown>).reflection = 42;
    expect(() => validateSurvivalJourney(r)).toThrow(/reflection must be a non-empty string/);
  });
});

/* ------------------------------------------------------------------ */
/* validateSurvivalJourney — rejection paths                           */
/* ------------------------------------------------------------------ */

describe("validateSurvivalJourney (rejection paths)", () => {
  it("rejects a non-object root", () => {
    expect(() => validateSurvivalJourney(null)).toThrow(/root must be an object/);
    expect(() => validateSurvivalJourney([])).toThrow(/root must be an object/);
  });

  it("rejects a missing summary", () => {
    const r = clone(buildRaw());
    delete r.summary;
    expect(() => validateSurvivalJourney(r)).toThrow(/summary must be an object/);
  });

  it("rejects a non-array steps", () => {
    const r = clone(buildRaw());
    r.steps = {};
    expect(() => validateSurvivalJourney(r)).toThrow(/steps must be an array/);
  });

  it("rejects when steps.length EXCEEDS total_steps (impossible downsample)", () => {
    const r = clone(buildRaw());
    // Fixture has 3 steps; a full count of 1 means the serialized steps
    // outnumber the full run — impossible under the downsample contract.
    (r.summary as Record<string, unknown>).total_steps = 1;
    expect(() => validateSurvivalJourney(r)).toThrow(/total_steps 1/);
  });

  it("accepts steps.length BELOW total_steps (a downsampled export)", () => {
    const r = clone(buildRaw());
    // Fixture has 3 serialized steps out of a 99-step full run — the normal
    // shape for any run over the exporter's max_steps chart budget.
    (r.summary as Record<string, unknown>).total_steps = 99;
    const f = validateSurvivalJourney(r);
    expect(f.steps).toHaveLength(3);
    expect(f.summary.total_steps).toBe(99);
  });

  it("rejects an invalid bet side on a step", () => {
    const r = clone(buildRaw());
    ((r.steps as Record<string, unknown>[])[0] as Record<string, unknown>).side = "MAYBE";
    expect(() => validateSurvivalJourney(r)).toThrow(/side must be "YES" or "NO"/);
  });

  it("rejects an out-of-range win_rate", () => {
    const r = clone(buildRaw());
    ((r.steps as Record<string, unknown>[])[0] as Record<string, unknown>).win_rate = 1.4;
    expect(() => validateSurvivalJourney(r)).toThrow(/win_rate must be in \[0, 1\]/);
  });

  it("rejects a step missing a weight key", () => {
    const r = clone(buildRaw());
    delete (
      ((r.steps as Record<string, unknown>[])[0] as Record<string, unknown>).weights as Record<
        string,
        unknown
      >
    ).rho;
    expect(() => validateSurvivalJourney(r)).toThrow(/weights\.rho/);
  });

  it("rejects a step missing a signal slot", () => {
    const r = clone(buildRaw());
    delete (
      ((r.steps as Record<string, unknown>[])[0] as Record<string, unknown>).signals as Record<
        string,
        unknown
      >
    ).rest_recency;
    expect(() => validateSurvivalJourney(r)).toThrow(/signals\.rest_recency/);
  });

  it("rejects a missing baseline key", () => {
    const r = clone(buildRaw());
    delete (r.baselines as Record<string, unknown>).random;
    expect(() => validateSurvivalJourney(r)).toThrow(/baselines\.random must be an array/);
  });

  it("rejects a non-2-element players tuple", () => {
    const r = clone(buildRaw());
    (((r.steps as Record<string, unknown>[])[0] as Record<string, unknown>).market as Record<
      string,
      unknown
    >).players = ["solo"];
    expect(() => validateSurvivalJourney(r)).toThrow(/players must be a 2-element array/);
  });

  it("throws a tagged SurvivalJourneyError", () => {
    try {
      validateSurvivalJourney(null);
      expect.unreachable("should have thrown");
    } catch (err) {
      expect(err).toBeInstanceOf(SurvivalJourneyError);
      expect((err as Error).message).toMatch(/^SurvivalJourney:/);
    }
  });
});

/* ------------------------------------------------------------------ */
/* Adapter                                                             */
/* ------------------------------------------------------------------ */

describe("adapter → generic chart view-models", () => {
  const f: SurvivalJourneyFixture = validateSurvivalJourney(buildRaw());

  it("adaptWeightViewModel emits one series per weight key over all steps", () => {
    const vm = adaptWeightViewModel(f);
    expect(vm.stepCount).toBe(3);
    expect(vm.series).toHaveLength(SURVIVAL_WEIGHT_KEYS.length);
    for (const s of vm.series) {
      expect(s.values).toHaveLength(3);
      expect(s.label.length).toBeGreaterThan(0);
    }
    // y-domain is the simplex [0,1].
    expect(vm.yMin).toBe(0);
    expect(vm.yMax).toBe(1);
    // The w_r series tracks the per-step post-update weight.
    const wr = vm.series.find((s) => s.key === "w_r")!;
    expect(wr.values[0]).toBeCloseTo(0.564, 3);
  });

  it("adaptPnlViewModel overlays learner + 3 baselines on a shared x-axis", () => {
    const vm = adaptPnlViewModel(f);
    // learner + static + random + always_favorite.
    expect(vm.series).toHaveLength(4);
    const learner = vm.series.find((s) => s.hero)!;
    expect(learner.key).toBe("learner");
    // Every series is resampled to the learner's step resolution.
    for (const s of vm.series) {
      expect(s.values).toHaveLength(vm.sampleCount);
    }
    expect(vm.sampleCount).toBe(3);
    // The learner's last sample is its final cum_pnl.
    expect(learner.values[learner.values.length - 1]).toBeCloseTo(30, 6);
    // Break-even reference + a y-domain that contains every sample.
    expect(vm.baselineY).toBe(0);
    expect(vm.yMin).toBeLessThanOrEqual(-3);
    expect(vm.yMax).toBeGreaterThanOrEqual(30);
  });

  it("adaptScrubberViewModel derives life boundaries + death markers", () => {
    const vm = adaptScrubberViewModel(f);
    expect(vm.stepCount).toBe(3);
    expect(vm.totalLives).toBe(2);
    expect(vm.lifeIdxByStep).toEqual([0, 0, 1]);
    // Two distinct lives → two boundaries (life 0 at step 0, life 1 at step 2).
    expect(vm.boundaries).toHaveLength(2);
    expect(vm.boundaries[0]).toBeCloseTo(0, 6);
    expect(vm.boundaries[1]).toBeCloseTo(1, 6); // step idx 2 / (3-1)
    // Exactly one death (life 0), positioned at its last step in the stream.
    expect(vm.deaths).toHaveLength(1);
    expect(vm.deaths[0]!.lifeIdx).toBe(0);
    expect(vm.deaths[0]!.stepIndex).toBe(1); // last step of life 0 is global idx 1
    expect(vm.deaths[0]!.cause).toBe("breath_depleted");
  });
});

/* ------------------------------------------------------------------ */
/* Helpers                                                             */
/* ------------------------------------------------------------------ */

describe("helpers", () => {
  const f = validateSurvivalJourney(buildRaw());

  it("bestLife resolves the summary.best_life index", () => {
    expect(bestLife(f)!.idx).toBe(1);
  });

  it("deathCount counts lives with a non-null death", () => {
    expect(deathCount(f)).toBe(1);
  });
});

/* ------------------------------------------------------------------ */
/* E2 STAR adapters — vitals / tombstones / survivor / step accessor   */
/* ------------------------------------------------------------------ */

describe("E2 vitals / tombstone adapters", () => {
  const f = validateSurvivalJourney(buildRaw());

  it("vitalsForStep scales breath against THIS life's peak breath", () => {
    // life 0 steps have breath 30 then 10 → peak 30. Step 0 sits at full.
    const v0 = vitalsForStep(f, 0)!;
    expect(v0.lifeIdx).toBe(0);
    expect(v0.lifePeakBreath).toBe(30);
    expect(v0.breathFrac).toBeCloseTo(1, 6);
    expect(v0.inDanger).toBe(false);
    // step 1 (breath 10 of peak 30 ≈ 0.33) is the last settled step of the
    // dying life → dying = true, which forces inDanger even though 0.33 > the
    // 0.25 danger band.
    const v1 = vitalsForStep(f, 1)!;
    expect(v1.breathFrac).toBeCloseTo(10 / 30, 6);
    expect(v1.dying).toBe(true);
    expect(v1.inDanger).toBe(true);
    // life 1 survives → its step is neither dying nor in danger.
    const v2 = vitalsForStep(f, 2)!;
    expect(v2.lifeIdx).toBe(1);
    expect(v2.dying).toBe(false);
    expect(v2.inDanger).toBe(false);
  });

  it("vitalsForStep clamps out-of-range indices", () => {
    expect(vitalsForStep(f, -5)!.lifeIdx).toBe(0);
    expect(vitalsForStep(f, 999)!.lifeIdx).toBe(1);
  });

  it("tombstones lists one entry per dead life with its final-step index", () => {
    const ts = tombstones(f);
    expect(ts).toHaveLength(1);
    expect(ts[0]!.lifeIdx).toBe(0);
    expect(ts[0]!.stepIndex).toBe(1); // last settled step of life 0
    expect(ts[0]!.cause).toBe("breath_depleted");
    expect(ts[0]!.bets).toBe(2);
    expect(ts[0]!.pnl).toBeCloseTo(-8, 6);
    expect(ts[0]!.tombstoneTokenId).toBe("0");
  });

  it("survivingLife returns the final non-death life", () => {
    expect(survivingLife(f)!.idx).toBe(1);
  });

  it("stepAt clamps and returns the right step", () => {
    expect(stepAt(f, 0)!.market.market_id).toBe("m1");
    expect(stepAt(f, 2)!.market.market_id).toBe("m3");
    expect(stepAt(f, 99)!.market.market_id).toBe("m3");
  });
});

/* ------------------------------------------------------------------ */
/* Real on-disk artifact (gitignored — guarded)                        */
/* ------------------------------------------------------------------ */

describe("real survival_journey.json artifact", () => {
  const realPath = path.join(process.cwd(), "public", "backtest", "survival_journey.json");
  const present = existsSync(realPath);
  const maybe = present ? it : it.skip;

  maybe("validates + adapts the real run without throwing", () => {
    const raw = JSON.parse(readFileSync(realPath, "utf-8")) as unknown;
    const f = validateSurvivalJourney(raw);
    // Serialized steps are a downsample; total_steps is the full-run count.
    expect(f.steps.length).toBeLessThanOrEqual(f.summary.total_steps);
    expect(f.summary.deaths).toBe(deathCount(f));

    const w = adaptWeightViewModel(f);
    expect(w.series).toHaveLength(SURVIVAL_WEIGHT_KEYS.length);
    expect(w.series[0]!.values).toHaveLength(f.steps.length);

    const p = adaptPnlViewModel(f);
    const learner = p.series.find((s) => s.hero)!;
    expect(learner.values[learner.values.length - 1]).toBeCloseTo(
      f.summary.learner_final_pnl,
      2,
    );

    const sc = adaptScrubberViewModel(f);
    expect(sc.deaths).toHaveLength(f.summary.deaths);
    expect(sc.totalLives).toBe(f.summary.lives);
  });
});
