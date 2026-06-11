/**
 * Static config-sweep loader — T-D-001 (Phase D).
 *
 * Surfaces the REAL signal-cached sweep result to the `/backtest` route. Pattern
 * mirrors `lib/load_training_journey.ts`: a static ES-module JSON import,
 * validated at module-eval time so the build catches schema drift before the
 * audience ever sees the dashboard.
 *
 * Producer: `dashboard/scripts/build_static_sweep.py` transcribes the optimal
 * seed + top-10 frontier + coverage from `reports/backtest/real_signal_sweep.md`,
 * then REPLAYS the optimal seed over the gitignored precomputed signal rows
 * (`reports/backtest/_signal_rows.json`) through the faithful
 * `DecisionEngine.decide` + settlement-PnL formula to recover a representative
 * sample of the literal bets it places, and writes
 * `public/backtest/static_sweep.json`.
 *
 * Consumer: this file. The JSON is small (~12 KB) and committed (NOT gitignored).
 */

import fixtureJson from "../public/backtest/static_sweep.json";

export const STATIC_SWEEP_SCHEMA_VERSION = "0.1.0" as const;

/** The 5 engine-slot keys, in stable display order.
 *
 *  Slot KEYS carry repurposed payloads (see the "Slot-name repurpose" caveat in
 *  `reports/backtest/real_signal_sweep.md`): `tennis_technical` = elo,
 *  `market_momentum` = CLOB momentum, `smart_money` = surface,
 *  `sentiment_llm` = h2h, `crowd_volume` = rest. */
export const SIGNAL_SLOT_KEYS = [
  "tennis_technical",
  "market_momentum",
  "smart_money",
  "sentiment_llm",
  "crowd_volume",
] as const;
export type SignalSlotKey = (typeof SIGNAL_SLOT_KEYS)[number];

/** Human-facing label per slot (reflects the repurposed payload, not the key). */
export const SIGNAL_SLOT_LABEL: Record<SignalSlotKey, string> = {
  tennis_technical: "ELO / Ranking",
  market_momentum: "CLOB Momentum",
  smart_money: "Surface",
  sentiment_llm: "Head-to-Head",
  crowd_volume: "Rest / Recency",
};

/** The fusion weights of a config. */
export interface SweepWeights {
  readonly w_r: number;
  readonly w_s: number;
  /** [α₁, α₂, α₃], sums to 1. */
  readonly alpha: readonly [number, number, number];
  /** [β₁, β₂], sums to 1. */
  readonly beta: readonly [number, number];
  readonly rho: number;
}

/** The bet-sizing / abstention knobs of a config (family ②). */
export interface SweepSizing {
  readonly max_breath_risk_pct: number;
  readonly min_confidence: number;
  readonly min_bet_size_usd: number;
}

/** The optimal SEED config + its rolled-up sweep metrics. */
export interface OptimalSeed {
  readonly weights: SweepWeights;
  readonly sizing: SweepSizing;
  readonly sharpe: number;
  readonly bets: number;
  /** Fraction in [0, 1]. */
  readonly win_rate: number;
  readonly net_pnl: number;
  readonly avg_bet_size: number;
}

/** One row of the robust frontier (top-10 by Sharpe, ≥50 bets). */
export interface FrontierRow {
  readonly rank: number;
  readonly sharpe: number;
  readonly net_pnl: number;
  /** Fraction in [0, 1]. */
  readonly win_rate: number;
  readonly bets: number;
  readonly w_r: number;
  readonly alpha: readonly [number, number, number];
  readonly rho: number;
  readonly max_breath_risk_pct: number;
  readonly min_bet_size_usd: number;
}

/** Per-bet side. */
export type BetSide = "YES" | "NO";
/** Settled market outcome. */
export type BetOutcome = "yes" | "no" | "void";

/** One representative bet the optimal seed places over the real cached signals. */
export interface SampleBet {
  readonly market_id: string;
  /** [player A surname, player B surname], from `parse_slug`. */
  readonly players: readonly [string, string];
  readonly surface: string;
  /** Mid-market entry price in [0, 1]. */
  readonly entry_price: number;
  readonly outcome: BetOutcome;
  /** The 5 slot scores at entry (each in [-1, 1]). */
  readonly signals: Readonly<Record<SignalSlotKey, number>>;
  readonly side: BetSide;
  readonly size: number;
  readonly pnl: number;
}

export interface StaticSweepFixture {
  readonly schema_version: string;
  readonly task_id: string;
  readonly sprint: string;
  /** Resolution coverage of the cassette universe, as a percentage (0–100). */
  readonly coverage_pct: number;
  readonly optimal_seed: OptimalSeed;
  readonly frontier: readonly FrontierRow[];
  readonly sample_bets: readonly SampleBet[];
}

/* ------------------------------------------------------------------ */
/* Validation                                                          */
/* ------------------------------------------------------------------ */

class StaticSweepError extends Error {
  constructor(message: string) {
    super(`StaticSweep: ${message}`);
    this.name = "StaticSweepError";
  }
}

function asObject(v: unknown, where: string): Record<string, unknown> {
  if (!v || typeof v !== "object" || Array.isArray(v)) {
    throw new StaticSweepError(`${where} must be an object`);
  }
  return v as Record<string, unknown>;
}

function asFinite(v: unknown, where: string): number {
  if (typeof v !== "number" || !Number.isFinite(v)) {
    throw new StaticSweepError(`${where} must be a finite number (got ${JSON.stringify(v)})`);
  }
  return v;
}

function asNonNegInt(v: unknown, where: string): number {
  if (typeof v !== "number" || !Number.isInteger(v) || v < 0) {
    throw new StaticSweepError(
      `${where} must be a non-negative integer (got ${JSON.stringify(v)})`,
    );
  }
  return v;
}

function asString(v: unknown, where: string): string {
  if (typeof v !== "string" || v.length === 0) {
    throw new StaticSweepError(`${where} must be a non-empty string`);
  }
  return v;
}

function asFraction(v: unknown, where: string): number {
  const n = asFinite(v, where);
  if (n < 0 || n > 1) {
    throw new StaticSweepError(`${where} must be in [0, 1] (got ${n})`);
  }
  return n;
}

/** Validate a fixed-length tuple of finite numbers. */
function asNumberTuple<N extends number>(
  v: unknown,
  len: N,
  where: string,
): number[] {
  if (!Array.isArray(v) || v.length !== len) {
    throw new StaticSweepError(
      `${where} must be an array of length ${len} (got ${
        Array.isArray(v) ? `length ${v.length}` : typeof v
      })`,
    );
  }
  return v.map((x, i) => asFinite(x, `${where}[${i}]`));
}

function validateWeights(raw: unknown, where: string): SweepWeights {
  const o = asObject(raw, where);
  const alpha = asNumberTuple(o.alpha, 3, `${where}.alpha`);
  const beta = asNumberTuple(o.beta, 2, `${where}.beta`);
  return {
    w_r: asFinite(o.w_r, `${where}.w_r`),
    w_s: asFinite(o.w_s, `${where}.w_s`),
    alpha: [alpha[0] as number, alpha[1] as number, alpha[2] as number],
    beta: [beta[0] as number, beta[1] as number],
    rho: asFinite(o.rho, `${where}.rho`),
  };
}

function validateSizing(raw: unknown, where: string): SweepSizing {
  const o = asObject(raw, where);
  return {
    max_breath_risk_pct: asFinite(o.max_breath_risk_pct, `${where}.max_breath_risk_pct`),
    min_confidence: asFinite(o.min_confidence, `${where}.min_confidence`),
    min_bet_size_usd: asFinite(o.min_bet_size_usd, `${where}.min_bet_size_usd`),
  };
}

function validateOptimalSeed(raw: unknown): OptimalSeed {
  const where = "optimal_seed";
  const o = asObject(raw, where);
  return {
    weights: validateWeights(o.weights, `${where}.weights`),
    sizing: validateSizing(o.sizing, `${where}.sizing`),
    sharpe: asFinite(o.sharpe, `${where}.sharpe`),
    bets: asNonNegInt(o.bets, `${where}.bets`),
    win_rate: asFraction(o.win_rate, `${where}.win_rate`),
    net_pnl: asFinite(o.net_pnl, `${where}.net_pnl`),
    avg_bet_size: asFinite(o.avg_bet_size, `${where}.avg_bet_size`),
  };
}

function validateFrontierRow(raw: unknown, idx: number): FrontierRow {
  const where = `frontier[${idx}]`;
  const o = asObject(raw, where);
  const alpha = asNumberTuple(o.alpha, 3, `${where}.alpha`);
  return {
    rank: asNonNegInt(o.rank, `${where}.rank`),
    sharpe: asFinite(o.sharpe, `${where}.sharpe`),
    net_pnl: asFinite(o.net_pnl, `${where}.net_pnl`),
    win_rate: asFraction(o.win_rate, `${where}.win_rate`),
    bets: asNonNegInt(o.bets, `${where}.bets`),
    w_r: asFinite(o.w_r, `${where}.w_r`),
    alpha: [alpha[0] as number, alpha[1] as number, alpha[2] as number],
    rho: asFinite(o.rho, `${where}.rho`),
    max_breath_risk_pct: asFinite(o.max_breath_risk_pct, `${where}.max_breath_risk_pct`),
    min_bet_size_usd: asFinite(o.min_bet_size_usd, `${where}.min_bet_size_usd`),
  };
}

function validateSide(v: unknown, where: string): BetSide {
  if (v !== "YES" && v !== "NO") {
    throw new StaticSweepError(`${where} must be "YES" or "NO" (got ${JSON.stringify(v)})`);
  }
  return v;
}

function validateOutcome(v: unknown, where: string): BetOutcome {
  if (v !== "yes" && v !== "no" && v !== "void") {
    throw new StaticSweepError(
      `${where} must be "yes" | "no" | "void" (got ${JSON.stringify(v)})`,
    );
  }
  return v;
}

function validateSignals(raw: unknown, where: string): Record<SignalSlotKey, number> {
  const o = asObject(raw, where);
  const out = {} as Record<SignalSlotKey, number>;
  for (const k of SIGNAL_SLOT_KEYS) {
    out[k] = asFinite(o[k], `${where}.${k}`);
  }
  return out;
}

function validateSampleBet(raw: unknown, idx: number): SampleBet {
  const where = `sample_bets[${idx}]`;
  const o = asObject(raw, where);
  const players = o.players;
  if (!Array.isArray(players) || players.length !== 2) {
    throw new StaticSweepError(`${where}.players must be a 2-element array`);
  }
  return {
    market_id: asString(o.market_id, `${where}.market_id`),
    players: [
      asString(players[0], `${where}.players[0]`),
      asString(players[1], `${where}.players[1]`),
    ],
    surface: asString(o.surface, `${where}.surface`),
    entry_price: asFraction(o.entry_price, `${where}.entry_price`),
    outcome: validateOutcome(o.outcome, `${where}.outcome`),
    signals: validateSignals(o.signals, `${where}.signals`),
    side: validateSide(o.side, `${where}.side`),
    size: asFinite(o.size, `${where}.size`),
    pnl: asFinite(o.pnl, `${where}.pnl`),
  };
}

/**
 * Validate an arbitrary value as a {@link StaticSweepFixture}, throwing a
 * {@link StaticSweepError} on the first schema violation. Exported so the loader
 * test can exercise the rejection paths against crafted malformed inputs (the
 * happy path is exercised by the bundled {@link STATIC_SWEEP} at module-eval).
 */
export function validateFixture(raw: unknown): StaticSweepFixture {
  const r = asObject(raw, "root");
  if (r.schema_version !== STATIC_SWEEP_SCHEMA_VERSION) {
    throw new StaticSweepError(
      `schema_version mismatch: expected '${STATIC_SWEEP_SCHEMA_VERSION}', got ${JSON.stringify(
        r.schema_version,
      )}`,
    );
  }
  if (!Array.isArray(r.frontier)) {
    throw new StaticSweepError("frontier must be an array");
  }
  if (!Array.isArray(r.sample_bets)) {
    throw new StaticSweepError("sample_bets must be an array");
  }
  return {
    schema_version: STATIC_SWEEP_SCHEMA_VERSION,
    task_id: asString(r.task_id, "task_id"),
    sprint: asString(r.sprint, "sprint"),
    coverage_pct: asFinite(r.coverage_pct, "coverage_pct"),
    optimal_seed: validateOptimalSeed(r.optimal_seed),
    frontier: r.frontier.map(validateFrontierRow),
    sample_bets: r.sample_bets.map(validateSampleBet),
  };
}

/** Validated, typed, bundled static-sweep fixture. */
export const STATIC_SWEEP: StaticSweepFixture = validateFixture(fixtureJson);

/* ------------------------------------------------------------------ */
/* Helpers                                                            */
/* ------------------------------------------------------------------ */

/** A win is a strictly-positive realised P&L; a void (`pnl === 0`) is not. */
export function isWin(bet: SampleBet): boolean {
  return bet.pnl > 0;
}

/** Count wins among the sample bets. */
export function sampleWinCount(fixture: StaticSweepFixture = STATIC_SWEEP): number {
  return fixture.sample_bets.reduce((acc, b) => acc + (isWin(b) ? 1 : 0), 0);
}

/** Sum of realised P&L across the sample bets. */
export function sampleNetPnl(fixture: StaticSweepFixture = STATIC_SWEEP): number {
  return fixture.sample_bets.reduce((acc, b) => acc + b.pnl, 0);
}
