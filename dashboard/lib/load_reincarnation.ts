/**
 * dashboard/lib/load_reincarnation.ts — Phase-2 reincarnation artifact types +
 * the pure structural validator (client-safe: no node built-ins).
 *
 * The artifact is written by `scripts/run_reincarnation.py` →
 * `agent/backtest/reincarnation.run_reincarnation_export`: N same-season
 * passes (weights + EMA carried, optional sanitized rebirth retrospectives)
 * followed by ONE learning-frozen cold-start pass on the held-out time window
 * + the three baselines, all under v3 physics. The validator is deliberately
 * minimal-structural — enough that the page can never render a legacy-physics
 * or shape-drifted artifact as if it were the experiment.
 */

export class ReincarnationError extends Error {}

export interface ReincarnationCurvePoint {
  readonly i: number;
  readonly cum_pnl: number;
}

export interface ReincarnationPassSummary {
  readonly pnl: number;
  readonly deaths: number;
  readonly lives: number;
  readonly settled: number;
  readonly coverage_pct: number;
  readonly win_rate: number;
}

export interface ReincarnationPass {
  readonly pass: number;
  readonly summary: ReincarnationPassSummary;
  readonly per_life_pnls: readonly number[];
  readonly start_weights: Readonly<Record<string, unknown>>;
  readonly terminal_weights: Readonly<Record<string, unknown>>;
  readonly curve: readonly ReincarnationCurvePoint[];
  readonly rebirth_note: string | null;
  readonly carry: {
    readonly ema_keys: readonly string[];
    readonly ema_size: number;
  };
}

export interface ReincarnationHoldout {
  readonly summary: ReincarnationPassSummary & {
    readonly learning_enabled: boolean;
  };
  readonly start_weights: Readonly<Record<string, unknown>>;
  readonly curve: readonly ReincarnationCurvePoint[];
  readonly baselines: {
    readonly static: number;
    readonly random: number;
    readonly always_favorite: number;
  };
}

export interface ReincarnationFixture {
  readonly experiment: "reincarnation";
  readonly provider: "numerical" | "ai";
  readonly physics: {
    readonly side_correct_pricing: boolean;
    readonly value_betting: boolean;
    readonly entry_price_floor: number;
    readonly max_bet_pnl_usd: number | null;
    readonly effective_entry_price_floor: number;
    readonly min_effective_entry_price: number | null;
    readonly min_edge: number;
    readonly kappa: number;
  };
  readonly split: {
    readonly train_rows: number;
    readonly holdout_rows: number;
    readonly train_fraction: number;
    readonly train_end_ts: string;
    readonly holdout_start_ts: string;
  };
  readonly knobs: Readonly<Record<string, number>>;
  readonly passes: readonly ReincarnationPass[];
  readonly holdout: ReincarnationHoldout;
}

function fail(msg: string): never {
  throw new ReincarnationError(`reincarnation artifact invalid: ${msg}`);
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function asNumber(v: unknown, label: string): number {
  if (typeof v !== "number" || !Number.isFinite(v)) fail(`${label} not a number`);
  return v;
}

function validateSummary(v: unknown, label: string): ReincarnationPassSummary {
  if (!isRecord(v)) fail(`${label} not an object`);
  for (const k of [
    "pnl",
    "deaths",
    "lives",
    "settled",
    "coverage_pct",
    "win_rate",
  ]) {
    asNumber(v[k], `${label}.${k}`);
  }
  return v as unknown as ReincarnationPassSummary;
}

function validateCurve(v: unknown, label: string): void {
  if (!Array.isArray(v)) fail(`${label} not an array`);
  for (const p of v) {
    if (!isRecord(p)) fail(`${label} point not an object`);
    asNumber(p.i, `${label}.i`);
    asNumber(p.cum_pnl, `${label}.cum_pnl`);
  }
}

/**
 * Structural validation — throws {@link ReincarnationError} on drift. The
 * physics booleans are HARD requirements: this page only ever presents
 * v3-physics data, so a legacy artifact must fail loudly, never render.
 */
export function validateReincarnation(data: unknown): ReincarnationFixture {
  if (!isRecord(data)) fail("root not an object");
  if (data.experiment !== "reincarnation") fail("experiment tag mismatch");
  if (data.provider !== "numerical" && data.provider !== "ai") {
    fail("provider must be 'numerical' | 'ai'");
  }
  const physics = data.physics;
  if (!isRecord(physics)) fail("physics missing");
  if (physics.side_correct_pricing !== true || physics.value_betting !== true) {
    fail("v3 physics flags must both be true");
  }
  const split = data.split;
  if (!isRecord(split)) fail("split missing");
  asNumber(split.train_rows, "split.train_rows");
  asNumber(split.holdout_rows, "split.holdout_rows");
  const passes = data.passes;
  if (!Array.isArray(passes) || passes.length < 1) fail("passes empty");
  passes.forEach((p, idx) => {
    if (!isRecord(p)) fail(`passes[${idx}] not an object`);
    asNumber(p.pass, `passes[${idx}].pass`);
    validateSummary(p.summary, `passes[${idx}].summary`);
    validateCurve(p.curve, `passes[${idx}].curve`);
    if (p.rebirth_note !== null && typeof p.rebirth_note !== "string") {
      fail(`passes[${idx}].rebirth_note must be string|null`);
    }
    if (!isRecord(p.carry) || !Array.isArray(p.carry.ema_keys)) {
      fail(`passes[${idx}].carry.ema_keys missing`);
    }
  });
  const holdout = data.holdout;
  if (!isRecord(holdout)) fail("holdout missing");
  const hs = validateSummary(holdout.summary, "holdout.summary");
  if ((hs as unknown as Record<string, unknown>).learning_enabled !== false) {
    fail("holdout.summary.learning_enabled must be false (frozen contract)");
  }
  validateCurve(holdout.curve, "holdout.curve");
  const baselines = holdout.baselines;
  if (!isRecord(baselines)) fail("holdout.baselines missing");
  for (const k of ["static", "random", "always_favorite"]) {
    asNumber(baselines[k], `holdout.baselines.${k}`);
  }
  return data as unknown as ReincarnationFixture;
}
