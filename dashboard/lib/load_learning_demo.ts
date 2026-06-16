/**
 * Stage-1 "能学" (can-learn) loader.
 *
 * Surfaces the self-evolution demo to the `/survival` route. Pattern mirrors
 * `lib/load_static_sweep.ts`: a static ES-module JSON import, validated at
 * module-eval so the build catches schema drift before the audience sees it.
 *
 * Producer: `dashboard/scripts/build_stage1.py` merges the gitignored
 * `reports/learning_demo/{pilot_frozen_ema,minimax}.json` + bakes in the
 * gain-sweep (spec §4.2) and the weight-ratchet (spec §6) into
 * `public/stage1/stage1_learning.json` (committed, ~few KB, NOT gitignored).
 *
 * Consumer: this file + the `LearningDemoPanel` component.
 */

import fixtureJson from "../public/stage1/stage1_learning.json";

export const LEARNING_DEMO_SCHEMA_VERSION = "0.1.0" as const;

/** The three demo arms, in display order. */
export const LEARNING_ARM_KEYS = ["frozen", "ema", "minimax"] as const;
export type LearningArmKey = (typeof LEARNING_ARM_KEYS)[number];

export interface ArmResult {
  readonly label: string;
  /** Fraction in [0, 1]. */
  readonly survival_rate: number;
  readonly mean_best_progress_pct: number;
  readonly mean_rise: number;
  /** Mean incarnation at which surviving seeds graduated (null if none did). */
  readonly mean_surviving_incarnation: number | null;
  /** Per-seed progress-% life-lines (variable length). */
  readonly curves: readonly (readonly number[])[];
}

export interface WeightTrajectory {
  readonly edge_slot_label: string;
  readonly noise_slot_label: string;
  readonly incarnations: readonly number[];
  readonly edge_weight: readonly number[];
  readonly noise_weight: readonly number[];
  readonly survived_at: number;
  readonly minimax_quote: string;
}

export interface GainSweepRow {
  readonly gain: number;
  readonly death_rate: number;
  /** Fraction in [0, 1]. */
  readonly survival_rate: number;
  readonly net_vs_seed: number;
}

export interface LearningDemoConfig {
  readonly gain: number;
  readonly n_rows: number;
  readonly max_incarnations: number;
  readonly edge_engine: string;
  readonly seeds: readonly number[];
  readonly economy: Readonly<Record<string, number | boolean>>;
}

export interface LearningDemoFixture {
  readonly schema_version: string;
  readonly config: LearningDemoConfig;
  readonly arms: Readonly<Record<LearningArmKey, ArmResult>>;
  readonly weight_trajectory: WeightTrajectory;
  readonly gain_sweep: readonly GainSweepRow[];
  readonly caveat: string;
}

/* ------------------------------------------------------------------ */
/* Validation                                                          */
/* ------------------------------------------------------------------ */

class LearningDemoError extends Error {
  constructor(message: string) {
    super(`LearningDemo: ${message}`);
    this.name = "LearningDemoError";
  }
}

function asObject(v: unknown, where: string): Record<string, unknown> {
  if (!v || typeof v !== "object" || Array.isArray(v)) {
    throw new LearningDemoError(`${where} must be an object`);
  }
  return v as Record<string, unknown>;
}

function asFinite(v: unknown, where: string): number {
  if (typeof v !== "number" || !Number.isFinite(v)) {
    throw new LearningDemoError(`${where} must be a finite number (got ${JSON.stringify(v)})`);
  }
  return v;
}

function asString(v: unknown, where: string): string {
  if (typeof v !== "string" || v.length === 0) {
    throw new LearningDemoError(`${where} must be a non-empty string`);
  }
  return v;
}

function asFraction(v: unknown, where: string): number {
  const n = asFinite(v, where);
  if (n < 0 || n > 1) {
    throw new LearningDemoError(`${where} must be in [0, 1] (got ${n})`);
  }
  return n;
}

function asNumberArray(v: unknown, where: string): number[] {
  if (!Array.isArray(v)) {
    throw new LearningDemoError(`${where} must be an array`);
  }
  return v.map((x, i) => asFinite(x, `${where}[${i}]`));
}

function validateArm(raw: unknown, where: string): ArmResult {
  const o = asObject(raw, where);
  if (!Array.isArray(o.curves)) {
    throw new LearningDemoError(`${where}.curves must be an array`);
  }
  const msi = o.mean_surviving_incarnation;
  return {
    label: asString(o.label, `${where}.label`),
    survival_rate: asFraction(o.survival_rate, `${where}.survival_rate`),
    mean_best_progress_pct: asFinite(o.mean_best_progress_pct, `${where}.mean_best_progress_pct`),
    mean_rise: asFinite(o.mean_rise, `${where}.mean_rise`),
    mean_surviving_incarnation:
      msi === null ? null : asFinite(msi, `${where}.mean_surviving_incarnation`),
    curves: o.curves.map((c, i) => asNumberArray(c, `${where}.curves[${i}]`)),
  };
}

function validateWeightTrajectory(raw: unknown): WeightTrajectory {
  const where = "weight_trajectory";
  const o = asObject(raw, where);
  return {
    edge_slot_label: asString(o.edge_slot_label, `${where}.edge_slot_label`),
    noise_slot_label: asString(o.noise_slot_label, `${where}.noise_slot_label`),
    incarnations: asNumberArray(o.incarnations, `${where}.incarnations`),
    edge_weight: asNumberArray(o.edge_weight, `${where}.edge_weight`),
    noise_weight: asNumberArray(o.noise_weight, `${where}.noise_weight`),
    survived_at: asFinite(o.survived_at, `${where}.survived_at`),
    minimax_quote: asString(o.minimax_quote, `${where}.minimax_quote`),
  };
}

function validateGainRow(raw: unknown, idx: number): GainSweepRow {
  const where = `gain_sweep[${idx}]`;
  const o = asObject(raw, where);
  return {
    gain: asFinite(o.gain, `${where}.gain`),
    death_rate: asFraction(o.death_rate, `${where}.death_rate`),
    survival_rate: asFraction(o.survival_rate, `${where}.survival_rate`),
    net_vs_seed: asFinite(o.net_vs_seed, `${where}.net_vs_seed`),
  };
}

function validateConfig(raw: unknown): LearningDemoConfig {
  const where = "config";
  const o = asObject(raw, where);
  const economy = asObject(o.economy, `${where}.economy`);
  const econ: Record<string, number | boolean> = {};
  for (const [k, val] of Object.entries(economy)) {
    econ[k] = typeof val === "boolean" ? val : asFinite(val, `${where}.economy.${k}`);
  }
  return {
    gain: asFinite(o.gain, `${where}.gain`),
    n_rows: asFinite(o.n_rows, `${where}.n_rows`),
    max_incarnations: asFinite(o.max_incarnations, `${where}.max_incarnations`),
    edge_engine: asString(o.edge_engine, `${where}.edge_engine`),
    seeds: asNumberArray(o.seeds, `${where}.seeds`),
    economy: econ,
  };
}

/**
 * Validate an arbitrary value as a {@link LearningDemoFixture}. Exported so the
 * loader test can exercise rejection paths; the happy path runs at module-eval
 * on the bundled {@link LEARNING_DEMO}.
 */
export function validateFixture(raw: unknown): LearningDemoFixture {
  const r = asObject(raw, "root");
  if (r.schema_version !== LEARNING_DEMO_SCHEMA_VERSION) {
    throw new LearningDemoError(
      `schema_version mismatch: expected '${LEARNING_DEMO_SCHEMA_VERSION}', got ${JSON.stringify(
        r.schema_version,
      )}`,
    );
  }
  if (!Array.isArray(r.gain_sweep)) {
    throw new LearningDemoError("gain_sweep must be an array");
  }
  const arms = asObject(r.arms, "arms");
  const builtArms = {} as Record<LearningArmKey, ArmResult>;
  for (const k of LEARNING_ARM_KEYS) {
    builtArms[k] = validateArm(arms[k], `arms.${k}`);
  }
  return {
    schema_version: LEARNING_DEMO_SCHEMA_VERSION,
    config: validateConfig(r.config),
    arms: builtArms,
    weight_trajectory: validateWeightTrajectory(r.weight_trajectory),
    gain_sweep: r.gain_sweep.map(validateGainRow),
    caveat: asString(r.caveat, "caveat"),
  };
}

/** Validated, typed, bundled Stage-1 learning fixture. */
export const LEARNING_DEMO: LearningDemoFixture = validateFixture(fixtureJson);
