/**
 * dashboard/lib/load_reincarnation.ts — Phase-2 GROUNDHOG artifact types +
 * the pure structural validator (client-safe: no node built-ins).
 *
 * Design v2 (user-locked): one incarnation = ONE life from the season's
 * first market; death → restart at market #1 carrying experience; the loop
 * runs until a life survives the whole train window or the incarnation cap;
 * a DEAD incarnation's profit is SCORED ZERO (permadeath economics — the
 * headline belongs to the surviving life only); then the frozen cold-start
 * holdout. Written by `run_groundhog_export`.
 *
 * The validator re-checks the orchestrator's cross-field scoring invariants
 * client-side (a stale or hand-edited deploy artifact must fail loudly, not
 * render a lie), and the v3 physics booleans are HARD requirements.
 */

export class ReincarnationError extends Error {}

export interface ReincarnationCurvePoint {
  readonly i: number;
  readonly cum_pnl: number;
}

export interface ReincarnationIncarnation {
  readonly incarnation: number;
  readonly died: boolean;
  /** Raw pnl the life held when it died (telemetry — forfeited on death). */
  readonly pnl_at_death: number;
  /** The permadeath-economics score: 0 when died, else == pnl_at_death. */
  readonly scored_pnl: number;
  readonly markets_seen: number;
  readonly progress_pct: number;
  readonly settled: number;
  readonly bets: number;
  readonly win_rate: number;
  readonly start_weights: Readonly<Record<string, unknown>>;
  readonly terminal_weights: Readonly<Record<string, unknown>>;
  readonly rebirth_note: string | null;
  readonly advisor: {
    readonly called: boolean;
    readonly proposals: number;
    readonly applied: number;
  };
  readonly carry: {
    readonly ema_keys: readonly string[];
    readonly ema_size: number;
  };
  /** Down-sampled cumulative curve — OPTIONAL (size guard keeps only the
   * first few, the survivor, and the final incarnation). */
  readonly curve?: readonly ReincarnationCurvePoint[];
}

export interface ReincarnationHoldout {
  readonly summary: {
    readonly pnl: number;
    readonly deaths: number;
    readonly lives: number;
    readonly settled: number;
    readonly coverage_pct: number;
    readonly win_rate: number;
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
  readonly design: "groundhog_day";
  readonly schema_version: 2;
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
  readonly scoring: string;
  readonly survived: boolean;
  readonly surviving_incarnation: number | null;
  readonly headline_pnl: number;
  readonly rebirth: {
    readonly expected: number;
    readonly calls: number;
    readonly productive: number;
    readonly empty_or_failed: number;
    readonly proposals: number;
    readonly applied: number;
  };
  readonly incarnations: readonly ReincarnationIncarnation[];
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

function validateCurve(v: unknown, label: string): void {
  if (!Array.isArray(v)) fail(`${label} not an array`);
  for (const p of v) {
    if (!isRecord(p)) fail(`${label} point not an object`);
    asNumber(p.i, `${label}.i`);
    asNumber(p.cum_pnl, `${label}.cum_pnl`);
  }
}

/**
 * Structural + cross-field validation — throws {@link ReincarnationError}
 * on drift OR on a scoring-rule violation. This page only ever presents the
 * groundhog design under v3 physics; anything else fails loudly.
 */
export function validateReincarnation(data: unknown): ReincarnationFixture {
  if (!isRecord(data)) fail("root not an object");
  if (data.experiment !== "reincarnation") fail("experiment tag mismatch");
  if (data.design !== "groundhog_day") fail("design must be 'groundhog_day'");
  if (data.schema_version !== 2) fail("schema_version must be 2");
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

  const incs = data.incarnations;
  if (!Array.isArray(incs) || incs.length < 1) fail("incarnations empty");
  incs.forEach((inc, idx) => {
    if (!isRecord(inc)) fail(`incarnations[${idx}] not an object`);
    asNumber(inc.incarnation, `incarnations[${idx}].incarnation`);
    if (typeof inc.died !== "boolean") fail(`incarnations[${idx}].died`);
    const scored = asNumber(inc.scored_pnl, `incarnations[${idx}].scored_pnl`);
    asNumber(inc.pnl_at_death, `incarnations[${idx}].pnl_at_death`);
    asNumber(inc.progress_pct, `incarnations[${idx}].progress_pct`);
    asNumber(inc.settled, `incarnations[${idx}].settled`);
    asNumber(inc.win_rate, `incarnations[${idx}].win_rate`);
    if (inc.died && scored !== 0) {
      fail(
        `scoring rule violated: dead incarnation ${idx + 1} carries ` +
          `scored_pnl ${scored}`,
      );
    }
    if (inc.rebirth_note !== null && typeof inc.rebirth_note !== "string") {
      fail(`incarnations[${idx}].rebirth_note must be string|null`);
    }
    if (!isRecord(inc.carry) || !Array.isArray(inc.carry.ema_keys)) {
      fail(`incarnations[${idx}].carry.ema_keys missing`);
    }
    // curve is OPTIONAL (size guard) — validated only when present.
    if (inc.curve !== undefined) {
      validateCurve(inc.curve, `incarnations[${idx}].curve`);
    }
  });

  // Cross-field scoring invariants (the permadeath-economics rule).
  if (typeof data.survived !== "boolean") fail("survived missing");
  const headline = asNumber(data.headline_pnl, "headline_pnl");
  const pointer = data.surviving_incarnation;
  if (data.survived === false) {
    if (headline !== 0) fail("capped-out artifact must have headline 0");
    if (pointer !== null) fail("capped-out artifact must have null survivor");
    if (!incs.every((i) => isRecord(i) && i.died === true)) {
      fail("capped-out artifact must be all-dead");
    }
  } else {
    if (typeof pointer !== "number" || pointer < 1 || pointer > incs.length) {
      fail("survived without a valid surviving_incarnation pointer");
    }
    const row = incs[pointer - 1] as Record<string, unknown>;
    if (row.died !== false) fail("survivor pointer targets a dead row");
    if (headline !== row.scored_pnl) {
      fail("headline must equal the survivor's scored_pnl");
    }
  }

  const rebirth = data.rebirth;
  if (!isRecord(rebirth)) fail("rebirth telemetry missing");
  for (const k of [
    "expected",
    "calls",
    "productive",
    "empty_or_failed",
    "proposals",
    "applied",
  ]) {
    asNumber(rebirth[k], `rebirth.${k}`);
  }

  const holdout = data.holdout;
  if (!isRecord(holdout)) fail("holdout missing");
  const hs = holdout.summary;
  if (!isRecord(hs)) fail("holdout.summary missing");
  for (const k of ["pnl", "deaths", "lives", "settled", "coverage_pct", "win_rate"]) {
    asNumber(hs[k], `holdout.summary.${k}`);
  }
  if (hs.learning_enabled !== false) {
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
