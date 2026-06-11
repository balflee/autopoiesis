/**
 * Training-journey loader — T-D-008 (sprint_7 Day 5-6).
 *
 * Surfaces the Phase-1 training-journey fixture to the `/backtest` route.
 * Pattern mirrors `lib/playback_loader.ts`: a static ES-module JSON import,
 * validated at module-eval time so the build catches schema drift before the
 * audience ever sees the dashboard.
 *
 * Producer: `dashboard/scripts/build_training_journey.py` reads
 * `reports/phase1/training_journey.jsonl` + `data/parquet/tennis_phase1.parquet`
 * + `reports/phase1/backtest_report.json`, joins the per-tick weights with
 * per-match metadata, recomputes deterministic per-archetype P&L curves, and
 * writes `public/backtest/training_journey.v0.1.0.json`.
 *
 * Consumer: this file. Loader fields are intentionally compact (`t`/`b`/`n`/`w`)
 * on the wire to shrink the bundle — we re-expose them under the human-readable
 * names {`tick`, `bankroll`, `bets`, `wins`} after validation.
 */

import fixtureJson from "../public/backtest/training_journey.v0.1.0.json";

export const TRAINING_JOURNEY_SCHEMA_VERSION = "0.1.0" as const;

/** Stable, render-order list of the six weight lines the chart shows.
 *  β₁ deliberately OMITTED — Phase 1 invariant (frozen at 0.0). */
export const WEIGHT_LINE_KEYS = [
  "w_r",
  "alpha_1",
  "alpha_2",
  "alpha_3",
  "beta_2",
  "rho",
] as const;
export type WeightLineKey = (typeof WEIGHT_LINE_KEYS)[number];

/** Greek/symbol display labels per weight. */
export const WEIGHT_LINE_LABEL: Record<WeightLineKey, string> = {
  w_r: "W_R",
  alpha_1: "α₁",
  alpha_2: "α₂",
  alpha_3: "α₃",
  beta_2: "β₂",
  rho: "ρ",
};

/** Four archetype baselines + the trained agent itself. */
export const ARCHETYPE_KEYS = [
  "random",
  "always_bet_favorite",
  "pessimist",
  "satisficer",
  "trained",
] as const;
export type ArchetypeKey = (typeof ARCHETYPE_KEYS)[number];

export const ARCHETYPE_LABEL: Record<ArchetypeKey, string> = {
  random: "Random",
  always_bet_favorite: "Always-Favorite",
  pessimist: "Pessimist",
  satisficer: "Satisficer",
  trained: "Agent (trained)",
};

/** One row in the journey — full 7-weight snapshot at tick `tick`. */
export interface TrainingJourneyTick {
  readonly tick: number;
  readonly epoch: number;
  readonly match_id: string;
  readonly w_r: number;
  readonly w_s: number;
  readonly alpha_1: number;
  readonly alpha_2: number;
  readonly alpha_3: number;
  readonly beta_1: number;
  readonly beta_2: number;
  readonly rho: number;
  readonly cumulative_loss: number;
  readonly tick_loss: number;
}

/** Match metadata keyed by `match_id`. */
export interface TrainingJourneyMatch {
  readonly player_a: string;
  readonly player_b: string;
  readonly surface: string;
  readonly tour_level: string;
  readonly best_of: number;
  readonly market_yes_price: number;
  /** (0.5 - market_yes_price) × 100, i.e. percentage-point edge if you back A. */
  readonly edge_pct: number;
  readonly outcome: number;
  readonly asof_ts: string;
}

/** Single bankroll sample on an archetype's P&L curve. */
export interface BaselineCurvePoint {
  readonly tick: number;
  readonly bankroll: number;
  readonly bets: number;
  readonly wins: number;
}

export interface PhaseInvariants {
  readonly beta_1_frozen: boolean;
  readonly beta_2_pinned: number;
  readonly rho_pinned: number;
  readonly w_r_pinned: number;
  readonly w_s_pinned: number;
}

export interface ArchetypeSummary {
  readonly name: string;
  readonly bets_placed: number;
  readonly bets_won: number;
  readonly final_bankroll_usd: number;
  readonly log_loss: number;
  readonly max_drawdown_usd: number;
  readonly mean_bankroll_usd: number;
  readonly mean_lifetime_matches: number;
  readonly win_rate: number;
}

export interface TrainingJourneyFixture {
  readonly schema_version: string;
  readonly task_id: string;
  readonly generated_at: string;
  readonly sprint: string;
  readonly phase: "PHASE_1_INFANCY";
  readonly starting_bankroll_usd: number;
  readonly flat_stake_usd: number;
  readonly n_ticks: number;
  readonly n_matches: number;
  readonly n_epochs: number;
  readonly phase1_invariants: PhaseInvariants;
  readonly final_archetype_results: readonly ArchetypeSummary[];
  readonly trained_summary: ArchetypeSummary;
  readonly ticks: readonly TrainingJourneyTick[];
  readonly matches: Readonly<Record<string, TrainingJourneyMatch>>;
  readonly baseline_curves: Readonly<Record<ArchetypeKey, readonly BaselineCurvePoint[]>>;
}

/* ------------------------------------------------------------------ */
/* Wire-format types (compact) → re-expand to friendly names           */
/* ------------------------------------------------------------------ */

interface WireBaselinePoint {
  readonly t: number;
  readonly b: number;
  readonly n: number;
  readonly w: number;
}

class TrainingJourneyError extends Error {
  constructor(message: string) {
    super(`TrainingJourney: ${message}`);
    this.name = "TrainingJourneyError";
  }
}

function asObject(v: unknown, where: string): Record<string, unknown> {
  if (!v || typeof v !== "object" || Array.isArray(v)) {
    throw new TrainingJourneyError(`${where} must be an object`);
  }
  return v as Record<string, unknown>;
}

function asFinite(v: unknown, where: string): number {
  if (typeof v !== "number" || !Number.isFinite(v)) {
    throw new TrainingJourneyError(`${where} must be a finite number (got ${JSON.stringify(v)})`);
  }
  return v;
}

function asNonNegInt(v: unknown, where: string): number {
  if (typeof v !== "number" || !Number.isInteger(v) || v < 0) {
    throw new TrainingJourneyError(
      `${where} must be a non-negative integer (got ${JSON.stringify(v)})`,
    );
  }
  return v;
}

function asString(v: unknown, where: string): string {
  if (typeof v !== "string" || v.length === 0) {
    throw new TrainingJourneyError(`${where} must be a non-empty string`);
  }
  return v;
}

function validateTick(raw: unknown, idx: number): TrainingJourneyTick {
  const where = `ticks[${idx}]`;
  const o = asObject(raw, where);
  const tick: TrainingJourneyTick = {
    tick: asNonNegInt(o.tick, `${where}.tick`),
    epoch: asNonNegInt(o.epoch, `${where}.epoch`),
    match_id: asString(o.match_id, `${where}.match_id`),
    w_r: asFinite(o.w_r, `${where}.w_r`),
    w_s: asFinite(o.w_s, `${where}.w_s`),
    alpha_1: asFinite(o.alpha_1, `${where}.alpha_1`),
    alpha_2: asFinite(o.alpha_2, `${where}.alpha_2`),
    alpha_3: asFinite(o.alpha_3, `${where}.alpha_3`),
    beta_1: asFinite(o.beta_1, `${where}.beta_1`),
    beta_2: asFinite(o.beta_2, `${where}.beta_2`),
    rho: asFinite(o.rho, `${where}.rho`),
    cumulative_loss: asFinite(o.cumulative_loss, `${where}.cumulative_loss`),
    tick_loss: asFinite(o.tick_loss, `${where}.tick_loss`),
  };
  if (Math.abs(tick.beta_1) > 1e-9) {
    throw new TrainingJourneyError(
      `${where}.beta_1 must be 0 in Phase 1 (got ${tick.beta_1})`,
    );
  }
  return tick;
}

function validateMatch(raw: unknown, id: string): TrainingJourneyMatch {
  const where = `matches['${id}']`;
  const o = asObject(raw, where);
  return {
    player_a: asString(o.player_a, `${where}.player_a`),
    player_b: asString(o.player_b, `${where}.player_b`),
    surface: asString(o.surface, `${where}.surface`),
    tour_level: asString(o.tour_level, `${where}.tour_level`),
    best_of: asNonNegInt(o.best_of, `${where}.best_of`),
    market_yes_price: asFinite(o.market_yes_price, `${where}.market_yes_price`),
    edge_pct: asFinite(o.edge_pct, `${where}.edge_pct`),
    outcome: asNonNegInt(o.outcome, `${where}.outcome`),
    asof_ts: asString(o.asof_ts, `${where}.asof_ts`),
  };
}

function validateBaselineCurve(
  raw: unknown,
  archetype: ArchetypeKey,
  nTicks: number,
): readonly BaselineCurvePoint[] {
  if (!Array.isArray(raw)) {
    throw new TrainingJourneyError(`baseline_curves[${archetype}] must be an array`);
  }
  if (raw.length !== nTicks) {
    throw new TrainingJourneyError(
      `baseline_curves[${archetype}] length ${raw.length} ≠ n_ticks ${nTicks}`,
    );
  }
  return raw.map((p, i) => {
    const where = `baseline_curves[${archetype}][${i}]`;
    const o = asObject(p, where) as unknown as WireBaselinePoint;
    return {
      tick: asNonNegInt(o.t, `${where}.t`),
      bankroll: asFinite(o.b, `${where}.b`),
      bets: asNonNegInt(o.n, `${where}.n`),
      wins: asNonNegInt(o.w, `${where}.w`),
    };
  });
}

function validateFixture(raw: unknown): TrainingJourneyFixture {
  const r = asObject(raw, "root");
  if (r.schema_version !== TRAINING_JOURNEY_SCHEMA_VERSION) {
    throw new TrainingJourneyError(
      `schema_version mismatch: expected '${TRAINING_JOURNEY_SCHEMA_VERSION}', got ${JSON.stringify(
        r.schema_version,
      )}`,
    );
  }
  if (r.phase !== "PHASE_1_INFANCY") {
    throw new TrainingJourneyError(`phase must be PHASE_1_INFANCY (got ${JSON.stringify(r.phase)})`);
  }
  const nTicks = asNonNegInt(r.n_ticks, "n_ticks");
  if (!Array.isArray(r.ticks) || r.ticks.length !== nTicks) {
    throw new TrainingJourneyError(
      `ticks length ${Array.isArray(r.ticks) ? r.ticks.length : "non-array"} ≠ n_ticks ${nTicks}`,
    );
  }
  const ticks = r.ticks.map(validateTick);
  const matchesRaw = asObject(r.matches, "matches");
  const matches: Record<string, TrainingJourneyMatch> = {};
  for (const [id, m] of Object.entries(matchesRaw)) {
    matches[id] = validateMatch(m, id);
  }
  const curvesRaw = asObject(r.baseline_curves, "baseline_curves");
  const baseline_curves: Record<ArchetypeKey, readonly BaselineCurvePoint[]> = {
    random: validateBaselineCurve(curvesRaw.random, "random", nTicks),
    always_bet_favorite: validateBaselineCurve(
      curvesRaw.always_bet_favorite,
      "always_bet_favorite",
      nTicks,
    ),
    pessimist: validateBaselineCurve(curvesRaw.pessimist, "pessimist", nTicks),
    satisficer: validateBaselineCurve(curvesRaw.satisficer, "satisficer", nTicks),
    trained: validateBaselineCurve(curvesRaw.trained, "trained", nTicks),
  };

  const inv = asObject(r.phase1_invariants, "phase1_invariants");

  return {
    schema_version: TRAINING_JOURNEY_SCHEMA_VERSION,
    task_id: asString(r.task_id, "task_id"),
    generated_at: asString(r.generated_at, "generated_at"),
    sprint: asString(r.sprint, "sprint"),
    phase: "PHASE_1_INFANCY",
    starting_bankroll_usd: asFinite(r.starting_bankroll_usd, "starting_bankroll_usd"),
    flat_stake_usd: asFinite(r.flat_stake_usd, "flat_stake_usd"),
    n_ticks: nTicks,
    n_matches: asNonNegInt(r.n_matches, "n_matches"),
    n_epochs: asNonNegInt(r.n_epochs, "n_epochs"),
    phase1_invariants: {
      beta_1_frozen: inv.beta_1_frozen === true,
      beta_2_pinned: asFinite(inv.beta_2_pinned, "phase1_invariants.beta_2_pinned"),
      rho_pinned: asFinite(inv.rho_pinned, "phase1_invariants.rho_pinned"),
      w_r_pinned: asFinite(inv.w_r_pinned, "phase1_invariants.w_r_pinned"),
      w_s_pinned: asFinite(inv.w_s_pinned, "phase1_invariants.w_s_pinned"),
    },
    final_archetype_results: Array.isArray(r.final_archetype_results)
      ? (r.final_archetype_results as ArchetypeSummary[])
      : [],
    trained_summary: (r.trained_summary as ArchetypeSummary) ?? ({} as ArchetypeSummary),
    ticks,
    matches,
    baseline_curves,
  };
}

/** Validated, typed, bundled training-journey fixture. */
export const TRAINING_JOURNEY: TrainingJourneyFixture = validateFixture(fixtureJson);

/* ------------------------------------------------------------------ */
/* Helpers                                                            */
/* ------------------------------------------------------------------ */

/** Return the (validated) tick at `tickIdx`, clamped into range. */
export function tickAt(fixture: TrainingJourneyFixture, tickIdx: number): TrainingJourneyTick {
  const n = fixture.ticks.length;
  if (n === 0) {
    throw new TrainingJourneyError("fixture has no ticks");
  }
  const idx = Math.max(0, Math.min(n - 1, Math.trunc(tickIdx)));
  return fixture.ticks[idx] as TrainingJourneyTick;
}

/** Look up the match metadata for a given tick (returns null for the
 *  synthetic `<initial>` row at tick 0). */
export function matchForTick(
  fixture: TrainingJourneyFixture,
  tick: TrainingJourneyTick,
): TrainingJourneyMatch | null {
  if (tick.match_id === "<initial>") return null;
  const m = fixture.matches[tick.match_id];
  return m ?? null;
}

/** Per-archetype bankroll at the given tick. Clamped to [0, n_ticks-1]. */
export function bankrollAt(
  fixture: TrainingJourneyFixture,
  archetype: ArchetypeKey,
  tickIdx: number,
): BaselineCurvePoint {
  const curve = fixture.baseline_curves[archetype];
  const n = curve.length;
  if (n === 0) {
    throw new TrainingJourneyError(`baseline_curves[${archetype}] is empty`);
  }
  const idx = Math.max(0, Math.min(n - 1, Math.trunc(tickIdx)));
  return curve[idx] as BaselineCurvePoint;
}

/** Final bankroll for each archetype (last point in each curve). */
export function finalBankrolls(
  fixture: TrainingJourneyFixture,
): Record<ArchetypeKey, number> {
  const out: Partial<Record<ArchetypeKey, number>> = {};
  for (const a of ARCHETYPE_KEYS) {
    const curve = fixture.baseline_curves[a];
    const last = curve[curve.length - 1];
    out[a] = last ? last.bankroll : fixture.starting_bankroll_usd;
  }
  return out as Record<ArchetypeKey, number>;
}
