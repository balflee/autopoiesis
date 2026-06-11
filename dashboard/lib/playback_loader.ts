/**
 * Playback fixture loader — T-D-006.
 *
 * Loads the curated PLAYBACK arc as a STATIC ES-module import. Next.js
 * inlines the JSON into the client bundle at build time so the demo's
 * critical 60 seconds has ZERO runtime network dependency (PRD §8).
 *
 * Schema mirror: `.dev/contracts/dashboard_playback_fixture.v0.1.0.json`.
 * Producer + consumer are both Track D. The loader rejects mismatched
 * `schema_version` at module-eval time so a broken fixture is caught by
 * the build, not by the audience.
 *
 * NOTE: distinct from `lib/memoryBank.ts` (which mirrors Track B's
 * MemoryBank producer payload via `dashboard_consciousness_stream`). The
 * PLAYBACK fixture surfaces α₁/α₂/α₃ + β₁/β₂ explicitly per PRD §6/§9.
 */

import fixtureJson from "../public/playback/phase2_day4_twitter_mistake.json";

/** Schema version the loader binds against. MUST match registry exactly. */
export const PLAYBACK_FIXTURE_SCHEMA_VERSION = "0.1.0" as const;

/** Narrative beat phase. Drives accent color and typography. */
export type PlaybackTickPhase = "lead_in" | "climax" | "outcome" | "reflection";

/** Per-tick 5-signal vector — PRD §6 dual-engine. */
export interface PlaybackSignals {
  /** α₁ — NBA-technical engine score [0,1]. */
  readonly alpha_1: number;
  /** α₂ — Polymarket-momentum engine score [0,1]. */
  readonly alpha_2: number;
  /** α₃ — Smart-money-flow engine score [0,1]. */
  readonly alpha_3: number;
  /** β₁ — Twitter-sentiment engine score [0,1] ("Twitter dominance" knob). */
  readonly beta_1: number;
  /** β₂ — Crowd-volume engine score [0,1]. */
  readonly beta_2: number;
}

/** The five canonical signal keys, in stable render order. */
export const SIGNAL_KEYS = [
  "alpha_1",
  "alpha_2",
  "alpha_3",
  "beta_1",
  "beta_2",
] as const;
export type SignalKey = (typeof SIGNAL_KEYS)[number];

/** Display label per signal — Greek + numeric subscript. */
export const SIGNAL_LABEL: Record<SignalKey, string> = {
  alpha_1: "α₁",
  alpha_2: "α₂",
  alpha_3: "α₃",
  beta_1: "β₁",
  beta_2: "β₂",
};

/** Bet decision at this tick. */
export interface PlaybackDecision {
  readonly action: "BET" | "NO_BET";
  readonly side?: string;
  readonly amount?: number;
  readonly score?: number;
  readonly edge?: number;
  readonly rho_eff?: number;
}

/** Settled-bet outcome. */
export interface PlaybackOutcome {
  readonly pnl: number;
  readonly settled_at: string;
  readonly result?: "WIN" | "LOSS" | "PUSH";
  readonly final_score?: string;
}

/** A single tick within a curated PLAYBACK arc. */
export interface PlaybackTick {
  readonly tick_id: number;
  readonly phase: PlaybackTickPhase;
  readonly day: number;
  readonly ts_utc: string;
  readonly narrative: string;
  readonly signals: PlaybackSignals;
  readonly decision: PlaybackDecision | null;
  readonly outcome: PlaybackOutcome | null;
  /** Optional — present only on the final reflection tick. */
  readonly reflection?: string;
  readonly dwell_ms: number;
}

/** Top-level curated arc. */
export interface PlaybackFixture {
  readonly schema_version: string;
  readonly snapshot_id: string;
  readonly title: string;
  readonly phase:
    | "PHASE_1_INFANCY"
    | "PHASE_2_APPRENTICE"
    | "PHASE_3_MASTER"
    | "PHASE_4_TERMINAL";
  readonly day: number;
  readonly synopsis: string;
  readonly ticks: readonly PlaybackTick[];
}

/* ------------------------------------------------------------------ */
/* Structural validation                                              */
/* ------------------------------------------------------------------ */

class PlaybackFixtureError extends Error {
  constructor(message: string) {
    super(`PlaybackFixture: ${message}`);
    this.name = "PlaybackFixtureError";
  }
}

function isFiniteNumberInRange(v: unknown, lo: number, hi: number): boolean {
  return typeof v === "number" && Number.isFinite(v) && v >= lo && v <= hi;
}

function validateSignals(raw: unknown, path: string): asserts raw is PlaybackSignals {
  if (!raw || typeof raw !== "object") {
    throw new PlaybackFixtureError(`${path}: signals must be an object`);
  }
  const s = raw as Record<string, unknown>;
  for (const k of SIGNAL_KEYS) {
    if (!isFiniteNumberInRange(s[k], 0, 1)) {
      throw new PlaybackFixtureError(
        `${path}: signals.${k} must be a finite number in [0,1] (got ${JSON.stringify(s[k])})`,
      );
    }
  }
}

function validateDecision(raw: unknown, path: string): asserts raw is PlaybackDecision {
  if (!raw || typeof raw !== "object") {
    throw new PlaybackFixtureError(`${path}: decision must be an object`);
  }
  const d = raw as Record<string, unknown>;
  if (d.action !== "BET" && d.action !== "NO_BET") {
    throw new PlaybackFixtureError(
      `${path}: decision.action must be 'BET' | 'NO_BET'`,
    );
  }
  if (d.amount !== undefined && !isFiniteNumberInRange(d.amount, 0, Number.MAX_SAFE_INTEGER)) {
    throw new PlaybackFixtureError(`${path}: decision.amount must be a non-negative finite number`);
  }
  if (d.rho_eff !== undefined && !isFiniteNumberInRange(d.rho_eff, -1, 1)) {
    throw new PlaybackFixtureError(`${path}: decision.rho_eff must be in [-1,1]`);
  }
}

function validateOutcome(raw: unknown, path: string): asserts raw is PlaybackOutcome {
  if (!raw || typeof raw !== "object") {
    throw new PlaybackFixtureError(`${path}: outcome must be an object`);
  }
  const o = raw as Record<string, unknown>;
  if (typeof o.pnl !== "number" || !Number.isFinite(o.pnl)) {
    throw new PlaybackFixtureError(`${path}: outcome.pnl must be a finite number`);
  }
  if (typeof o.settled_at !== "string" || o.settled_at.length === 0) {
    throw new PlaybackFixtureError(`${path}: outcome.settled_at must be a non-empty string`);
  }
}

function validateTick(raw: unknown, idx: number): asserts raw is PlaybackTick {
  const path = `ticks[${idx}]`;
  if (!raw || typeof raw !== "object") {
    throw new PlaybackFixtureError(`${path}: must be an object`);
  }
  const t = raw as Record<string, unknown>;
  if (typeof t.tick_id !== "number" || !Number.isInteger(t.tick_id) || t.tick_id < 0) {
    throw new PlaybackFixtureError(`${path}.tick_id must be a non-negative integer`);
  }
  if (
    t.phase !== "lead_in" &&
    t.phase !== "climax" &&
    t.phase !== "outcome" &&
    t.phase !== "reflection"
  ) {
    throw new PlaybackFixtureError(`${path}.phase invalid (got ${JSON.stringify(t.phase)})`);
  }
  if (typeof t.day !== "number" || !Number.isInteger(t.day) || t.day < 0) {
    throw new PlaybackFixtureError(`${path}.day must be a non-negative integer`);
  }
  if (typeof t.ts_utc !== "string" || t.ts_utc.length === 0) {
    throw new PlaybackFixtureError(`${path}.ts_utc must be a non-empty string`);
  }
  if (typeof t.narrative !== "string" || t.narrative.length === 0) {
    throw new PlaybackFixtureError(`${path}.narrative must be a non-empty string`);
  }
  validateSignals(t.signals, `${path}.signals`);
  if (t.decision !== null && t.decision !== undefined) {
    validateDecision(t.decision, `${path}.decision`);
  }
  if (t.outcome !== null && t.outcome !== undefined) {
    validateOutcome(t.outcome, `${path}.outcome`);
  }
  if (t.reflection !== undefined && (typeof t.reflection !== "string" || t.reflection.length === 0)) {
    throw new PlaybackFixtureError(`${path}.reflection must be a non-empty string when present`);
  }
  if (
    typeof t.dwell_ms !== "number" ||
    !Number.isInteger(t.dwell_ms) ||
    t.dwell_ms < 0 ||
    t.dwell_ms > 60_000
  ) {
    throw new PlaybackFixtureError(`${path}.dwell_ms must be an integer in [0,60000]`);
  }
}

/** Validates the fixture and returns the typed value. Throws on any drift. */
export function validatePlaybackFixture(raw: unknown): PlaybackFixture {
  if (!raw || typeof raw !== "object") {
    throw new PlaybackFixtureError("root must be an object");
  }
  const f = raw as Record<string, unknown>;
  if (f.schema_version !== PLAYBACK_FIXTURE_SCHEMA_VERSION) {
    throw new PlaybackFixtureError(
      `schema_version mismatch: expected '${PLAYBACK_FIXTURE_SCHEMA_VERSION}', got ${JSON.stringify(
        f.schema_version,
      )}`,
    );
  }
  if (typeof f.snapshot_id !== "string" || !/^[a-z0-9_]+$/.test(f.snapshot_id)) {
    throw new PlaybackFixtureError("snapshot_id must be lower-snake-case");
  }
  if (typeof f.title !== "string" || f.title.length === 0) {
    throw new PlaybackFixtureError("title must be a non-empty string");
  }
  const validPhases = new Set([
    "PHASE_1_INFANCY",
    "PHASE_2_APPRENTICE",
    "PHASE_3_MASTER",
    "PHASE_4_TERMINAL",
  ]);
  if (typeof f.phase !== "string" || !validPhases.has(f.phase)) {
    throw new PlaybackFixtureError(`phase invalid (got ${JSON.stringify(f.phase)})`);
  }
  if (typeof f.day !== "number" || !Number.isInteger(f.day) || f.day < 0) {
    throw new PlaybackFixtureError("day must be a non-negative integer");
  }
  if (typeof f.synopsis !== "string" || f.synopsis.length === 0) {
    throw new PlaybackFixtureError("synopsis must be a non-empty string");
  }
  if (!Array.isArray(f.ticks) || f.ticks.length === 0) {
    throw new PlaybackFixtureError("ticks must be a non-empty array");
  }
  if (f.ticks.length > 12) {
    throw new PlaybackFixtureError(
      `ticks length ${f.ticks.length} exceeds the 12-tick legibility ceiling`,
    );
  }
  for (let i = 0; i < f.ticks.length; i += 1) {
    validateTick(f.ticks[i], i);
  }
  return f as unknown as PlaybackFixture;
}

/**
 * Bundled curated arc for the demo's 1:30 – 2:30 PLAYBACK window (PRD §9).
 * Loaded via static ES-module import — Next.js inlines the JSON into the
 * client bundle at build time. Zero network fetch at demo time.
 *
 * Throws synchronously at module evaluation if the bundled fixture drifts
 * away from `schema_version 0.1.0`.
 */
export const PHASE2_DAY4_TWITTER_MISTAKE: PlaybackFixture =
  validatePlaybackFixture(fixtureJson);

/* ------------------------------------------------------------------ */
/* Dominant-signal helper                                             */
/* ------------------------------------------------------------------ */

export interface DominantSignal {
  /** Key of the signal with the highest value on this tick. */
  readonly key: SignalKey;
  /** Value of the dominant signal. */
  readonly value: number;
  /** value - max(other_signals). */
  readonly delta: number;
  /** Whether the dominance exceeds the highlight threshold (0.3 per PRD §8). */
  readonly highlighted: boolean;
}

/** PRD §8 dominant-signal highlight threshold. */
export const DOMINANT_SIGNAL_DELTA_THRESHOLD = 0.3;

/**
 * Compute the dominant signal for a tick. `highlighted` flips true when
 * `(max - second_max) > 0.3` — exactly the PRD §8 amber-highlight rule.
 *
 * Pure function — same input, same output. Tested deterministically.
 */
export function dominantSignal(signals: PlaybackSignals): DominantSignal {
  let topKey: SignalKey = SIGNAL_KEYS[0];
  let topVal = signals[topKey];
  let secondVal = Number.NEGATIVE_INFINITY;
  for (const k of SIGNAL_KEYS) {
    const v = signals[k];
    if (v > topVal) {
      secondVal = topVal;
      topVal = v;
      topKey = k;
    } else if (v > secondVal) {
      secondVal = v;
    }
  }
  const delta = topVal - (Number.isFinite(secondVal) ? secondVal : 0);
  return {
    key: topKey,
    value: topVal,
    delta,
    highlighted: delta > DOMINANT_SIGNAL_DELTA_THRESHOLD,
  };
}
