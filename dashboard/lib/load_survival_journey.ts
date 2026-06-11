/**
 * Survival-journey loader + adapter — E1 (Phase D/E, codex M5).
 *
 * Surfaces the REAL L5 survival-season run to the `/survival` route. This is a
 * SEPARATE data shape from the Phase-1 training journey: across a single seed
 * the agent lives, DIES (breath depleted → permadeath), respawns, and — once
 * the WeightUpdater is wired — slowly learns to survive. The run produces a
 * single `survival_journey.json` artifact (A4 CLI export).
 *
 * ── codex M5 directives this module obeys ───────────────────────────────────
 *   1. Do NOT cast this into `TrainingJourneyFixture`. That fixture carries
 *      Phase-1-only fields (epochs, β₁-frozen invariant, archetype simplex)
 *      that DO NOT EXIST in a survival run; casting would fabricate them.
 *      Instead we declare a first-class {@link SurvivalJourneyFixture}.
 *   2. Do NOT pump survival data through the live `useWsStore` — that store is
 *      the Page-3 (/live) sandbox-telemetry channel; feeding it backtest data
 *      pollutes the live view. Survival is a static, file-backed read.
 *   3. The reusable chart primitives (WeightEvolutionChart / PnLBaselineChart /
 *      BacktestScrubber) were Phase-1-fixture-bound; they have been refactored
 *      to accept GENERIC view-model props. This module owns the ADAPTER that
 *      maps a {@link SurvivalJourneyFixture} into those generic view-models.
 *
 * ── Provenance / wiring ─────────────────────────────────────────────────────
 *   Producer: `python -m … export` (A4 CLI) writes
 *   `public/backtest/survival_journey.json` (~4 MB, GITIGNORED — it is a
 *   generated artifact, never committed). Because it is large + gitignored we
 *   deliberately do NOT statically `import` it as an ES module (that would
 *   inline 4 MB into the bundle and break the build wherever the file is
 *   absent). The `/survival` route reads it from disk at request/build time via
 *   the server companion `load_survival_journey.server.ts`, then runs it
 *   through {@link validateSurvivalJourney} here.
 *
 * Consumer: this file exports pure, DOM-free `validate*` + adapter functions so
 * they can be unit-tested against crafted inputs (TDD), and re-used by both the
 * server loader and any future client hydration.
 */

/* ------------------------------------------------------------------ */
/* Slot / weight keys                                                  */
/* ------------------------------------------------------------------ */

/** The five engine-slot signal keys, in stable display order. Shared with the
 *  static-sweep fixture — same five tennis payloads (elo / CLOB momentum /
 *  surface / h2h / rest), repurposed onto the legacy engine-slot names. */
export const SURVIVAL_SIGNAL_KEYS = [
  "tennis_technical",
  "market_momentum",
  "smart_money",
  "sentiment_llm",
  "crowd_volume",
] as const;
export type SurvivalSignalKey = (typeof SURVIVAL_SIGNAL_KEYS)[number];

/** Human-facing label per slot (reflects the repurposed payload, not the key). */
export const SURVIVAL_SIGNAL_LABEL: Record<SurvivalSignalKey, string> = {
  tennis_technical: "ELO / Ranking",
  market_momentum: "CLOB Momentum",
  smart_money: "Surface",
  sentiment_llm: "Head-to-Head",
  crowd_volume: "Rest / Recency",
};

/** The eight fusion-weight keys carried at every learner step. Survival weights
 *  use the `_0`-indexed simplex naming (alpha_0..2, beta_0..1), distinct from
 *  the Phase-1 `_1`-indexed scheme — a key reason NOT to cast across fixtures. */
export const SURVIVAL_WEIGHT_KEYS = [
  "w_r",
  "w_s",
  "alpha_0",
  "alpha_1",
  "alpha_2",
  "beta_0",
  "beta_1",
  "rho",
] as const;
export type SurvivalWeightKey = (typeof SURVIVAL_WEIGHT_KEYS)[number];

/** Greek/symbol display labels per weight. */
export const SURVIVAL_WEIGHT_LABEL: Record<SurvivalWeightKey, string> = {
  w_r: "W_R",
  w_s: "W_S",
  alpha_0: "α₀",
  alpha_1: "α₁",
  alpha_2: "α₂",
  beta_0: "β₀",
  beta_1: "β₁",
  rho: "ρ",
};

/** The three non-learning baselines run over the same market universe. */
export const SURVIVAL_BASELINE_KEYS = [
  "static",
  "random",
  "always_favorite",
] as const;
export type SurvivalBaselineKey = (typeof SURVIVAL_BASELINE_KEYS)[number];

export const SURVIVAL_BASELINE_LABEL: Record<SurvivalBaselineKey, string> = {
  static: "Static Seed",
  random: "Random",
  always_favorite: "Always-Favorite",
};

/* ------------------------------------------------------------------ */
/* Fixture types                                                       */
/* ------------------------------------------------------------------ */

export type SurvivalSide = "YES" | "NO";
export type SurvivalOutcome = "yes" | "no" | "void";

/** The seed sizing/abstention knobs the whole run is born with. */
export interface SurvivalSeedSizing {
  readonly max_breath_risk_pct: number;
  readonly min_bet_size_usd: number;
  readonly min_confidence: number;
}

export type SurvivalWeights = Readonly<Record<SurvivalWeightKey, number>>;

export interface SurvivalSeed {
  readonly max_breath_risk_pct: number;
  readonly min_bet_size_usd: number;
  readonly min_confidence: number;
  readonly weights: SurvivalWeights;
}

export interface SurvivalSummary {
  /** 0-based index of the longest-surviving / best life. */
  readonly best_life: number;
  /** Number of deaths over the season. */
  readonly deaths: number;
  /** Cumulative learner P&L at the final step (== last step's cum_pnl). */
  readonly learner_final_pnl: number;
  /** learner_final_pnl − static_final_pnl. */
  readonly learning_vs_static_delta: number;
  /** Number of lives lived (deaths + 1 survivor, typically). */
  readonly lives: number;
  /** Final cumulative P&L of the static-seed baseline. */
  readonly static_final_pnl: number;
  /** Number of learner steps (bets) across all lives. */
  readonly total_steps: number;
  /**
   * OPTIONAL self-disclosure keys (newer exporters only — absent on archived
   * pre-realism-rules runs, which is exactly how the finetune log tells the
   * versions apart). `null` = the rule was explicitly off for that run.
   */
  /** Entry-price floor the run's universe was filtered at (e.g. 0.05). */
  readonly entry_price_floor?: number | null;
  /** Per-bet PROFIT ceiling (USD) the run enforced (e.g. 100). */
  readonly max_bet_pnl_usd?: number | null;
  /** AI runs: count of auto-approved weight deltas actually APPLIED. */
  readonly proposals_applied?: number;
}

/** How a life ended. `null` for the final surviving life. */
export interface SurvivalDeath {
  /** Breath remaining at death (≈ 0 for breath_depleted). */
  readonly breath: number;
  /** The death cause, e.g. "breath_depleted". */
  readonly cause: string;
  /** Global market tick at which the kill landed. (JSON key: `last_tick`.) */
  readonly tick: number;
  /** On-chain Tombstone mint tx hash, if any. */
  readonly kill_tx_hash: string;
  /** Tombstone NFT token id, if any. */
  readonly tombstone_token_id: string;
}

/** One life in the season. */
export interface SurvivalLife {
  readonly idx: number;
  readonly bets: number;
  /** Per-life realised P&L. */
  readonly pnl: number;
  /** Null for the final surviving life; otherwise how it died. */
  readonly death: SurvivalDeath | null;
  readonly final_bankroll_usd: number;
  readonly final_breath: number;
  readonly settlements: number;
  readonly start_ts: string;
}

export interface SurvivalMarket {
  readonly entry_price: number;
  readonly market_id: string;
  readonly outcome: SurvivalOutcome;
  /** [player A surname, player B surname]. */
  readonly players: readonly [string, string];
  readonly slug: string;
  readonly surface: string;
}

/** One learner step (a placed bet) at global ordering `globalIndex`. */
export interface SurvivalStep {
  /** Life index this step belongs to (JSON `idx` == `life_idx`). */
  readonly life_idx: number;
  readonly market: SurvivalMarket;
  readonly side: SurvivalSide;
  readonly size: number;
  readonly pnl: number;
  /** Cumulative learner P&L across ALL lives up to and including this step. */
  readonly cum_pnl: number;
  readonly breath: number;
  /** Rolling win rate in [0,1]. */
  readonly win_rate: number;
  /** Fusion weights AFTER this step's learning update. */
  readonly weights: SurvivalWeights;
  /** Fusion weights BEFORE this step's learning update. */
  readonly weights_before: SurvivalWeights;
  /** The five slot scores at entry (each in [-1,1]). */
  readonly signals: Readonly<Record<SurvivalSignalKey, number>>;
  /**
   * OPTIONAL Page-2 timeline annotation (Phase B / B3). Present ONLY on steps
   * where the L6 reflect→advisor closure fired — e.g.
   * `"reflected (tick_interval) #<id> -> proposed 1 proposal (pending approval)"`.
   * The numerical journey omits the key entirely (NoOp advisor); the AI journey
   * populates it on the steps where Gemini reflection drove a proposal.
   */
  readonly reflection?: string;
}

/** One point on a baseline's cumulative-P&L curve (one per market in the
 *  universe, whether or not the baseline bet on it). */
export interface SurvivalBaselinePoint {
  /** 0-based market index in the universe. */
  readonly idx: number;
  readonly cum_pnl: number;
  readonly pnl: number;
  readonly is_bet: boolean;
  readonly market_id: string;
  readonly side: SurvivalSide | null;
  readonly size: number;
}

export interface SurvivalJourneyFixture {
  readonly seed: SurvivalSeed;
  readonly summary: SurvivalSummary;
  readonly lives: readonly SurvivalLife[];
  readonly steps: readonly SurvivalStep[];
  readonly baselines: Readonly<
    Record<SurvivalBaselineKey, readonly SurvivalBaselinePoint[]>
  >;
}

/* ------------------------------------------------------------------ */
/* Validation                                                          */
/* ------------------------------------------------------------------ */

export class SurvivalJourneyError extends Error {
  constructor(message: string) {
    super(`SurvivalJourney: ${message}`);
    this.name = "SurvivalJourneyError";
  }
}

function asObject(v: unknown, where: string): Record<string, unknown> {
  if (!v || typeof v !== "object" || Array.isArray(v)) {
    throw new SurvivalJourneyError(`${where} must be an object`);
  }
  return v as Record<string, unknown>;
}

function asArray(v: unknown, where: string): unknown[] {
  if (!Array.isArray(v)) {
    throw new SurvivalJourneyError(`${where} must be an array`);
  }
  return v;
}

function asFinite(v: unknown, where: string): number {
  if (typeof v !== "number" || !Number.isFinite(v)) {
    throw new SurvivalJourneyError(
      `${where} must be a finite number (got ${JSON.stringify(v)})`,
    );
  }
  return v;
}

function asNonNegInt(v: unknown, where: string): number {
  if (typeof v !== "number" || !Number.isInteger(v) || v < 0) {
    throw new SurvivalJourneyError(
      `${where} must be a non-negative integer (got ${JSON.stringify(v)})`,
    );
  }
  return v;
}

function asString(v: unknown, where: string): string {
  if (typeof v !== "string" || v.length === 0) {
    throw new SurvivalJourneyError(`${where} must be a non-empty string`);
  }
  return v;
}

/** A string that may legitimately be empty (e.g. a zeroed tx hash). */
function asAnyString(v: unknown, where: string): string {
  if (typeof v !== "string") {
    throw new SurvivalJourneyError(`${where} must be a string`);
  }
  return v;
}

function asFraction(v: unknown, where: string): number {
  const n = asFinite(v, where);
  if (n < 0 || n > 1) {
    throw new SurvivalJourneyError(`${where} must be in [0, 1] (got ${n})`);
  }
  return n;
}

function validateSide(v: unknown, where: string): SurvivalSide {
  if (v !== "YES" && v !== "NO") {
    throw new SurvivalJourneyError(
      `${where} must be "YES" or "NO" (got ${JSON.stringify(v)})`,
    );
  }
  return v;
}

function validateOutcome(v: unknown, where: string): SurvivalOutcome {
  if (v !== "yes" && v !== "no" && v !== "void") {
    throw new SurvivalJourneyError(
      `${where} must be "yes" | "no" | "void" (got ${JSON.stringify(v)})`,
    );
  }
  return v;
}

function validateWeights(raw: unknown, where: string): SurvivalWeights {
  const o = asObject(raw, where);
  const out = {} as Record<SurvivalWeightKey, number>;
  for (const k of SURVIVAL_WEIGHT_KEYS) {
    out[k] = asFinite(o[k], `${where}.${k}`);
  }
  return out;
}

function validateSignals(
  raw: unknown,
  where: string,
): Record<SurvivalSignalKey, number> {
  const o = asObject(raw, where);
  const out = {} as Record<SurvivalSignalKey, number>;
  for (const k of SURVIVAL_SIGNAL_KEYS) {
    out[k] = asFinite(o[k], `${where}.${k}`);
  }
  return out;
}

function validateSeed(raw: unknown): SurvivalSeed {
  const where = "seed";
  const o = asObject(raw, where);
  return {
    max_breath_risk_pct: asFinite(o.max_breath_risk_pct, `${where}.max_breath_risk_pct`),
    min_bet_size_usd: asFinite(o.min_bet_size_usd, `${where}.min_bet_size_usd`),
    min_confidence: asFinite(o.min_confidence, `${where}.min_confidence`),
    weights: validateWeights(o.weights, `${where}.weights`),
  };
}

/** Optional rule-disclosure key: absent → undefined, null → null, else finite. */
function asOptFiniteOrNull(v: unknown, where: string): number | null | undefined {
  if (v === undefined) return undefined;
  if (v === null) return null;
  return asFinite(v, where);
}

function validateSummary(raw: unknown): SurvivalSummary {
  const where = "summary";
  const o = asObject(raw, where);
  return {
    best_life: asNonNegInt(o.best_life, `${where}.best_life`),
    deaths: asNonNegInt(o.deaths, `${where}.deaths`),
    learner_final_pnl: asFinite(o.learner_final_pnl, `${where}.learner_final_pnl`),
    learning_vs_static_delta: asFinite(
      o.learning_vs_static_delta,
      `${where}.learning_vs_static_delta`,
    ),
    lives: asNonNegInt(o.lives, `${where}.lives`),
    static_final_pnl: asFinite(o.static_final_pnl, `${where}.static_final_pnl`),
    total_steps: asNonNegInt(o.total_steps, `${where}.total_steps`),
    // Optional self-disclosure (newer exporters; tolerated absent on archives).
    entry_price_floor: asOptFiniteOrNull(o.entry_price_floor, `${where}.entry_price_floor`),
    max_bet_pnl_usd: asOptFiniteOrNull(o.max_bet_pnl_usd, `${where}.max_bet_pnl_usd`),
    proposals_applied:
      o.proposals_applied === undefined
        ? undefined
        : asNonNegInt(o.proposals_applied, `${where}.proposals_applied`),
  };
}

function validateDeath(raw: unknown, where: string): SurvivalDeath {
  const o = asObject(raw, where);
  return {
    breath: asFinite(o.breath, `${where}.breath`),
    cause: asString(o.cause, `${where}.cause`),
    // Real JSON key is `last_tick`; fall back to `tick` for forward-compat.
    tick: asNonNegInt(o.last_tick ?? o.tick, `${where}.last_tick`),
    kill_tx_hash:
      o.kill_tx_hash === undefined ? "" : asAnyString(o.kill_tx_hash, `${where}.kill_tx_hash`),
    tombstone_token_id:
      o.tombstone_token_id === undefined
        ? ""
        : asAnyString(o.tombstone_token_id, `${where}.tombstone_token_id`),
  };
}

function validateLife(raw: unknown, idx: number): SurvivalLife {
  const where = `lives[${idx}]`;
  const o = asObject(raw, where);
  return {
    idx: asNonNegInt(o.idx, `${where}.idx`),
    bets: asNonNegInt(o.bets, `${where}.bets`),
    pnl: asFinite(o.pnl, `${where}.pnl`),
    death: o.death === null || o.death === undefined ? null : validateDeath(o.death, `${where}.death`),
    final_bankroll_usd: asFinite(o.final_bankroll_usd, `${where}.final_bankroll_usd`),
    final_breath: asFinite(o.final_breath, `${where}.final_breath`),
    settlements: asNonNegInt(o.settlements, `${where}.settlements`),
    start_ts: asString(o.start_ts, `${where}.start_ts`),
  };
}

function validateMarket(raw: unknown, where: string): SurvivalMarket {
  const o = asObject(raw, where);
  const players = o.players;
  if (!Array.isArray(players) || players.length !== 2) {
    throw new SurvivalJourneyError(`${where}.players must be a 2-element array`);
  }
  return {
    entry_price: asFraction(o.entry_price, `${where}.entry_price`),
    market_id: asString(o.market_id, `${where}.market_id`),
    outcome: validateOutcome(o.outcome, `${where}.outcome`),
    players: [
      asString(players[0], `${where}.players[0]`),
      asString(players[1], `${where}.players[1]`),
    ],
    slug: asString(o.slug, `${where}.slug`),
    surface: asString(o.surface, `${where}.surface`),
  };
}

function validateStep(raw: unknown, gi: number): SurvivalStep {
  const where = `steps[${gi}]`;
  const o = asObject(raw, where);
  const step: SurvivalStep = {
    life_idx: asNonNegInt(o.life_idx, `${where}.life_idx`),
    market: validateMarket(o.market, `${where}.market`),
    side: validateSide(o.side, `${where}.side`),
    size: asFinite(o.size, `${where}.size`),
    pnl: asFinite(o.pnl, `${where}.pnl`),
    cum_pnl: asFinite(o.cum_pnl, `${where}.cum_pnl`),
    breath: asFinite(o.breath, `${where}.breath`),
    win_rate: asFraction(o.win_rate, `${where}.win_rate`),
    weights: validateWeights(o.weights, `${where}.weights`),
    weights_before: validateWeights(o.weights_before, `${where}.weights_before`),
    signals: validateSignals(o.signals, `${where}.signals`),
  };
  // `reflection?` is OPTIONAL (B3): present only on AI-run steps where the
  // reflect→advisor closure fired. Absent key → numerical run (byte-unchanged).
  if (o.reflection !== undefined && o.reflection !== null) {
    return { ...step, reflection: asString(o.reflection, `${where}.reflection`) };
  }
  return step;
}

function validateBaselinePoint(
  raw: unknown,
  base: SurvivalBaselineKey,
  i: number,
): SurvivalBaselinePoint {
  const where = `baselines.${base}[${i}]`;
  const o = asObject(raw, where);
  return {
    idx: asNonNegInt(o.idx, `${where}.idx`),
    cum_pnl: asFinite(o.cum_pnl, `${where}.cum_pnl`),
    pnl: asFinite(o.pnl, `${where}.pnl`),
    is_bet: o.is_bet === true,
    market_id: asString(o.market_id, `${where}.market_id`),
    side: o.side === null || o.side === undefined ? null : validateSide(o.side, `${where}.side`),
    size: asFinite(o.size, `${where}.size`),
  };
}

function validateBaselines(
  raw: unknown,
): Record<SurvivalBaselineKey, readonly SurvivalBaselinePoint[]> {
  const o = asObject(raw, "baselines");
  const out = {} as Record<SurvivalBaselineKey, SurvivalBaselinePoint[]>;
  for (const base of SURVIVAL_BASELINE_KEYS) {
    const arr = asArray(o[base], `baselines.${base}`);
    out[base] = arr.map((p, i) => validateBaselinePoint(p, base, i));
  }
  return out;
}

/**
 * Validate an arbitrary value as a {@link SurvivalJourneyFixture}, throwing a
 * {@link SurvivalJourneyError} on the first schema violation.
 *
 * Exported (rather than auto-binding a bundled fixture at module-eval, the way
 * the small committed static-sweep does) because the survival JSON is large +
 * gitignored: the route reads it from disk and pipes it through here.
 */
export function validateSurvivalJourney(raw: unknown): SurvivalJourneyFixture {
  const r = asObject(raw, "root");
  const summary = validateSummary(r.summary);
  const livesRaw = asArray(r.lives, "lives");
  const stepsRaw = asArray(r.steps, "steps");

  const fixture: SurvivalJourneyFixture = {
    seed: validateSeed(r.seed),
    summary,
    lives: livesRaw.map(validateLife),
    steps: stepsRaw.map(validateStep),
    baselines: validateBaselines(r.baselines),
  };

  // Cross-field consistency: the exporter's contract is `steps` = a
  // DOWNSAMPLE of the full run (chart-size cap, `_downsample` server-side)
  // while `summary.total_steps` keeps the FULL count for drill-down. So the
  // serialized steps may be FEWER than total_steps (any run over the export's
  // max_steps budget) — but can never legitimately EXCEED it.
  if (fixture.steps.length > summary.total_steps) {
    throw new SurvivalJourneyError(
      `steps length ${fixture.steps.length} > summary.total_steps ${summary.total_steps}`,
    );
  }
  return fixture;
}

/* ================================================================== */
/* ADAPTER — survival fixture → reusable-chart view-models            */
/* ================================================================== */
/*
 * The reusable chart primitives consume GENERIC, fixture-agnostic view-models
 * (see `WeightSeriesViewModel`, `PnLSeriesViewModel`, `ScrubberViewModel` in the
 * component files). The adapter below is the SINGLE bridge from a survival run
 * to those view-models — nothing else in the survival page reaches into the raw
 * fixture for chart data. This is the codex-M5 "adapter, NOT cast" boundary.
 */

/** A named series of [x, y] samples for the weight-evolution chart. */
export interface AdaptedWeightSeries {
  readonly key: string;
  readonly label: string;
  /** Per-step weight value, indexed by global step order. */
  readonly values: readonly number[];
}

/** Generic weight-evolution view-model (one entry per global step). */
export interface AdaptedWeightViewModel {
  /** Number of x samples (== number of learner steps). */
  readonly stepCount: number;
  /** Display sub-title, e.g. "842 bets · 7 lives". */
  readonly subtitle: string;
  /** Weight series in stable render order. */
  readonly series: readonly AdaptedWeightSeries[];
  /** y-domain — survival simplex weights live in [0,1]. */
  readonly yMin: number;
  readonly yMax: number;
}

/** A named cumulative-P&L curve for the baseline chart. */
export interface AdaptedPnlSeries {
  readonly key: string;
  readonly label: string;
  /** Cumulative P&L sampled across a shared, normalised x-axis. */
  readonly values: readonly number[];
  /** True for the learner (hero) line. */
  readonly hero: boolean;
}

/** Generic P&L view-model — all curves resampled onto a shared x-axis. */
export interface AdaptedPnlViewModel {
  /** Length of every series' `values` (shared x resolution). */
  readonly sampleCount: number;
  readonly subtitle: string;
  readonly series: readonly AdaptedPnlSeries[];
  readonly yMin: number;
  readonly yMax: number;
  /** The reference y (P&L == 0, i.e. break-even start). */
  readonly baselineY: number;
}

/** A death marker on the scrubber, positioned by global step index. */
export interface AdaptedDeathMarker {
  /** 0-based global step index nearest the death. */
  readonly stepIndex: number;
  readonly lifeIdx: number;
  readonly cause: string;
}

/** Generic scrubber view-model — drives the BacktestScrubber over steps. */
export interface AdaptedScrubberViewModel {
  readonly stepCount: number;
  /** Per-step life index — used to draw life-boundary markers + the label. */
  readonly lifeIdxByStep: readonly number[];
  /**
   * Life-boundary fractions in [0,1] (one per life transition, incl. step 0).
   * Named `boundaries` so this view-model is structurally assignable to the
   * generic {@link ScrubberViewModel} the BacktestScrubber actually reads.
   */
  readonly boundaries: readonly number[];
  /** Death markers (one per life that died). */
  readonly deaths: readonly AdaptedDeathMarker[];
  readonly totalLives: number;
}

/** Stable render order for the weight lines. */
const WEIGHT_RENDER_ORDER: readonly SurvivalWeightKey[] = SURVIVAL_WEIGHT_KEYS;

/**
 * Adapt the per-step fusion weights into the generic weight view-model.
 * x is the index into the SERIALIZED (possibly downsampled) `steps`
 * (0..steps.length-1; `summary.total_steps` keeps the full-run count); each
 * weight key becomes a series of post-update values.
 */
export function adaptWeightViewModel(
  fixture: SurvivalJourneyFixture,
): AdaptedWeightViewModel {
  const series: AdaptedWeightSeries[] = WEIGHT_RENDER_ORDER.map((k) => ({
    key: k,
    label: SURVIVAL_WEIGHT_LABEL[k],
    values: fixture.steps.map((s) => s.weights[k]),
  }));
  return {
    stepCount: fixture.steps.length,
    subtitle: `${fixture.summary.total_steps.toLocaleString()} bets · ${fixture.summary.lives} lives`,
    series,
    yMin: 0,
    yMax: 1,
  };
}

/**
 * Resample a baseline's cumulative-P&L curve (one point per market in the
 * universe, length L) onto exactly `sampleCount` evenly-spaced samples so it
 * shares an x-axis with the learner curve (length = step count). Nearest-point
 * sampling — these are monotone-ish cumulative curves so this reads cleanly.
 */
function resampleCumPnl(
  points: readonly { readonly cum_pnl: number }[],
  sampleCount: number,
): number[] {
  if (sampleCount <= 0) return [];
  if (points.length === 0) return new Array<number>(sampleCount).fill(0);
  const out: number[] = [];
  for (let i = 0; i < sampleCount; i += 1) {
    const frac = sampleCount === 1 ? 1 : i / (sampleCount - 1);
    const srcIdx = Math.min(points.length - 1, Math.round(frac * (points.length - 1)));
    out.push(points[srcIdx]!.cum_pnl);
  }
  return out;
}

/**
 * Adapt the learner + three baselines into the generic P&L view-model.
 *
 * The learner curve is the per-step `cum_pnl` over the SERIALIZED (possibly
 * downsampled) `steps` (length = steps.length, <= summary.total_steps). Each
 * baseline curve has one point per market in the universe (length ~4925); we
 * resample every series onto the learner's x resolution so they overlay on a
 * single shared axis. The shared x-axis is "progress through the season",
 * 0→1; exact tick alignment across heterogeneous-length curves is not the
 * story — the divergence of the learner from the static seed is.
 */
export function adaptPnlViewModel(
  fixture: SurvivalJourneyFixture,
): AdaptedPnlViewModel {
  const sampleCount = Math.max(fixture.steps.length, 1);
  const learner = fixture.steps.map((s) => s.cum_pnl);
  const learnerSamples =
    learner.length > 0 ? learner : new Array<number>(sampleCount).fill(0);

  const series: AdaptedPnlSeries[] = [
    {
      key: "static",
      label: SURVIVAL_BASELINE_LABEL.static,
      values: resampleCumPnl(fixture.baselines.static, sampleCount),
      hero: false,
    },
    {
      key: "random",
      label: SURVIVAL_BASELINE_LABEL.random,
      values: resampleCumPnl(fixture.baselines.random, sampleCount),
      hero: false,
    },
    {
      key: "always_favorite",
      label: SURVIVAL_BASELINE_LABEL.always_favorite,
      values: resampleCumPnl(fixture.baselines.always_favorite, sampleCount),
      hero: false,
    },
    {
      key: "learner",
      label: "Learner (survives)",
      values: learnerSamples,
      hero: true,
    },
  ];

  let lo = 0;
  let hi = 0;
  for (const s of series) {
    for (const v of s.values) {
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
  }
  const pad = Math.max((hi - lo) * 0.08, 5);

  return {
    sampleCount,
    subtitle: `learner +${money0(fixture.summary.learning_vs_static_delta)} vs static seed`,
    series,
    yMin: lo - pad,
    yMax: hi + pad,
    baselineY: 0,
  };
}

/**
 * Adapt the lives → the generic scrubber view-model. The scrubber walks the
 * global step axis; life boundaries + death markers are derived from
 * `step.life_idx` transitions and the per-life `death` payloads.
 */
export function adaptScrubberViewModel(
  fixture: SurvivalJourneyFixture,
): AdaptedScrubberViewModel {
  const stepCount = fixture.steps.length;
  const lifeIdxByStep = fixture.steps.map((s) => s.life_idx);

  // Life-boundary fractions: first step index of each distinct life.
  const boundaries: number[] = [];
  let prev = -1;
  fixture.steps.forEach((s, i) => {
    if (s.life_idx !== prev) {
      boundaries.push(stepCount > 1 ? i / (stepCount - 1) : 0);
      prev = s.life_idx;
    }
  });

  // Map each dead life to the FINAL step index of that life (the step at which
  // it expired in the step stream).
  const lastStepOfLife = new Map<number, number>();
  fixture.steps.forEach((s, i) => {
    lastStepOfLife.set(s.life_idx, i);
  });
  const deaths: AdaptedDeathMarker[] = [];
  for (const life of fixture.lives) {
    if (life.death) {
      const stepIndex = lastStepOfLife.get(life.idx);
      deaths.push({
        stepIndex: stepIndex ?? 0,
        lifeIdx: life.idx,
        cause: life.death.cause,
      });
    }
  }

  return {
    stepCount,
    lifeIdxByStep,
    boundaries,
    deaths,
    totalLives: fixture.summary.lives,
  };
}

/* ================================================================== */
/* ADAPTER — vitals / tombstones / current-bet (the E2 STAR widgets)  */
/* ================================================================== */
/*
 * E1 wired the three chart primitives. E2 turns /survival into the showpiece:
 * a BREATH/vitals gauge that drains toward each death, a TOMBSTONE strip for
 * the lives that died, and a per-step current-bet view. These adapters are the
 * single bridge from the raw fixture to those widgets — same "adapter, NOT
 * cast" boundary as the chart view-models, and DOM-free so they unit-test.
 */

/** A vitals snapshot for one global step — drives the breath gauge. */
export interface SurvivalVitals {
  /** Life this step belongs to. */
  readonly lifeIdx: number;
  /** Raw breath at this step. */
  readonly breath: number;
  /** Breath as a fraction in [0,1] of THIS life's peak observed breath. */
  readonly breathFrac: number;
  /** Peak breath observed across this life's steps (the gauge's "full"). */
  readonly lifePeakBreath: number;
  /** Rolling win-rate at this step, [0,1]. */
  readonly winRate: number;
  /** Cumulative learner P&L through this step. */
  readonly cumPnl: number;
  /** True once breath sits in the danger band (≤ DANGER_FRAC of peak). */
  readonly inDanger: boolean;
  /** True when this step is the LAST settled step of a life that died. */
  readonly dying: boolean;
}

/** Below this fraction of a life's peak breath the gauge reads "danger". */
const VITALS_DANGER_FRAC = 0.25;

/**
 * Per-life peak breath, indexed by life idx. Used to scale the breath gauge so
 * each life is read against its OWN high-water mark — life 6 peaks ~9 254 while
 * the doomed early lives barely clear their 35-breath spawn, so a global max
 * would flatten every fragile life to an invisible sliver.
 */
function lifePeakBreathMap(fixture: SurvivalJourneyFixture): Map<number, number> {
  const peak = new Map<number, number>();
  for (const s of fixture.steps) {
    const cur = peak.get(s.life_idx) ?? 0;
    if (s.breath > cur) peak.set(s.life_idx, s.breath);
  }
  return peak;
}

/** The global step indices that are the FINAL settled step of a dead life. */
function dyingStepIndices(fixture: SurvivalJourneyFixture): Set<number> {
  const lastStepOfLife = new Map<number, number>();
  fixture.steps.forEach((s, i) => lastStepOfLife.set(s.life_idx, i));
  const dying = new Set<number>();
  for (const life of fixture.lives) {
    if (life.death) {
      const i = lastStepOfLife.get(life.idx);
      if (i !== undefined) dying.add(i);
    }
  }
  return dying;
}

/**
 * Compute the {@link SurvivalVitals} for a given global step index. Clamps the
 * index into range; returns `null` only for an empty run.
 */
export function vitalsForStep(
  fixture: SurvivalJourneyFixture,
  stepIndex: number,
): SurvivalVitals | null {
  if (fixture.steps.length === 0) return null;
  const i = Math.max(0, Math.min(fixture.steps.length - 1, Math.trunc(stepIndex)));
  const step = fixture.steps[i]!;
  const peak = Math.max(lifePeakBreathMap(fixture).get(step.life_idx) ?? step.breath, 1e-9);
  const breathFrac = Math.max(0, Math.min(1, step.breath / peak));
  const dying = dyingStepIndices(fixture).has(i);
  return {
    lifeIdx: step.life_idx,
    breath: step.breath,
    breathFrac,
    lifePeakBreath: peak,
    winRate: step.win_rate,
    cumPnl: step.cum_pnl,
    inDanger: breathFrac <= VITALS_DANGER_FRAC || dying,
    dying,
  };
}

/** A tombstone for one life that died — drives the death/tombstone strip. */
export interface SurvivalTombstone {
  readonly lifeIdx: number;
  readonly cause: string;
  /** Global step index of the life's final settled bet (scrubber position). */
  readonly stepIndex: number;
  /** Realised P&L over the life. */
  readonly pnl: number;
  /** Number of bets the life placed before it died. */
  readonly bets: number;
  readonly killTxHash: string;
  readonly tombstoneTokenId: string;
}

/**
 * The ordered list of tombstones (one per life that died), each tagged with the
 * global step index at which it expired so the page can highlight the active
 * tombstone as the scrubber crosses it.
 */
export function tombstones(
  fixture: SurvivalJourneyFixture,
): readonly SurvivalTombstone[] {
  const lastStepOfLife = new Map<number, number>();
  fixture.steps.forEach((s, i) => lastStepOfLife.set(s.life_idx, i));
  const out: SurvivalTombstone[] = [];
  for (const life of fixture.lives) {
    if (!life.death) continue;
    out.push({
      lifeIdx: life.idx,
      cause: life.death.cause,
      stepIndex: lastStepOfLife.get(life.idx) ?? 0,
      pnl: life.pnl,
      bets: life.bets,
      killTxHash: life.death.kill_tx_hash,
      tombstoneTokenId: life.death.tombstone_token_id,
    });
  }
  return out;
}

/**
 * The surviving (final, non-death) life, if any. The hero of the story — the
 * life that learned to breathe.
 */
export function survivingLife(
  fixture: SurvivalJourneyFixture,
): SurvivalLife | null {
  for (let i = fixture.lives.length - 1; i >= 0; i -= 1) {
    const l = fixture.lives[i]!;
    if (!l.death) return l;
  }
  return null;
}

/** Safe accessor for the step at a (clamped) global index. */
export function stepAt(
  fixture: SurvivalJourneyFixture,
  stepIndex: number,
): SurvivalStep | null {
  if (fixture.steps.length === 0) return null;
  const i = Math.max(0, Math.min(fixture.steps.length - 1, Math.trunc(stepIndex)));
  return fixture.steps[i]!;
}

/* ------------------------------------------------------------------ */
/* Small helpers                                                       */
/* ------------------------------------------------------------------ */

/** Whole-dollar money formatter for adapter subtitles. */
function money0(n: number): string {
  return `$${Math.round(Math.abs(n)).toLocaleString()}`;
}

/** The longest-surviving life by step count (ties → lowest idx). */
export function bestLife(fixture: SurvivalJourneyFixture): SurvivalLife | null {
  if (fixture.lives.length === 0) return null;
  const target = fixture.summary.best_life;
  return fixture.lives.find((l) => l.idx === target) ?? fixture.lives[0]!;
}

/** Total deaths across the season (lives with a non-null death payload). */
export function deathCount(fixture: SurvivalJourneyFixture): number {
  return fixture.lives.reduce((acc, l) => acc + (l.death ? 1 : 0), 0);
}
