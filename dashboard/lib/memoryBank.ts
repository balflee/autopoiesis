/**
 * MemoryBank — Track D's consumer view of the per-tick narrative stream.
 *
 * Schema is mirrored by the producer contract
 * `.dev/contracts/dashboard_consciousness_stream.v0.1.0.json`. The producer
 * side (Track B, T-B-001) populates real ticks from on-chain + decision-loop
 * state; this file describes the SHAPE only and bundles the curated
 * Phase 2 Day 4 snapshot for the PLAYBACK demo.
 *
 * Sprint_1 scope (T-D-001): PLAYBACK ONLY. LIVE WebSocket plumbing is sprint_2.
 */

import snapshotJson from "../public/snapshots/phase2_day4_first_twitter_mistake.json";

/** Narrative phase of a single tick within a curated arc. */
export type TickPhase = "lead_in" | "climax" | "outcome" | "reflection";

/** Agent vitals as recorded at the tick boundary. */
export interface Vitals {
  /** Remaining BREATH energy units (PRD §4). */
  readonly breath: number;
  /** Polymarket bankroll, USD. */
  readonly bankroll: number;
  /** Days since current Phase began. */
  readonly phase_age_days: number;
}

/**
 * Dual-engine weights. `w_r` + `w_s` SHOULD sum to ~1.0; we do not enforce
 * the constraint in the type because reflective updates can transiently
 * drift before normalisation. PRD §6 / TECHNICAL_PLAN §5.3 DualEngineMeter.
 */
export interface Weights {
  /** Rule-engine weight. */
  readonly w_r: number;
  /** Signal-engine weight (Twitter sentiment, narrative momentum). */
  readonly w_s: number;
  /** Risk-engine α. */
  readonly alpha: number;
  /** Signal-engine β₁ (the "Twitter dominance" knob in PRD §9). */
  readonly beta: number;
  /** Cross-engine reconciliation ρ. */
  readonly rho: number;
}

/** Agent's bet decision for the tick (null when no decision was taken). */
export interface Decision {
  readonly action: "BET" | "NO_BET";
  readonly side?: string;
  readonly size_usd?: number;
  readonly edge_pct?: number;
  readonly kelly_fraction?: number;
}

/** Settled outcome for a prior BET (null until the market resolves). */
export interface Outcome {
  readonly pnl_usd: number;
  readonly result: "WIN" | "LOSS" | "PUSH";
  readonly final_score?: string;
}

/** A single tick within a curated PLAYBACK arc. */
export interface MemoryBankTick {
  readonly tick: number;
  readonly phase: TickPhase;
  /** Per-tick dwell time in milliseconds — auto-play uses this directly. */
  readonly dwell_ms: number;
  readonly timestamp: string;
  readonly vitals: Vitals;
  readonly weights: Weights;
  /** Diary text — rendered ≥28px per PRD §8 projector-readability rule. */
  readonly diary: string;
  readonly decision: Decision | null;
  readonly outcome: Outcome | null;
  /** Optional UI hints: ['signal_dominance', 'loss', 'rule_learned', ...]. */
  readonly highlights: readonly string[];
}

/** Top-level curated snapshot (one self-contained narrative arc). */
export interface MemoryBankSnapshot {
  readonly snapshot_id: string;
  readonly schema_version: string;
  readonly agent_id: string;
  readonly phase: string;
  readonly day_index: number;
  readonly title: string;
  readonly synopsis: string;
  readonly ticks: readonly MemoryBankTick[];
}

/**
 * Bundled snapshot for the demo's 1:30 – 2:30 PLAYBACK window
 * (PRD §9). Imported as a JSON module so it inlines into the Next.js
 * client bundle at build time — zero network fetch at demo time.
 */
export const PHASE2_DAY4_SNAPSHOT: MemoryBankSnapshot =
  snapshotJson as MemoryBankSnapshot;

/**
 * Narrow runtime guard — throws if a candidate object is missing
 * structural fields the PLAYBACK widget needs. Cheap, predictable, no zod.
 */
export function assertSnapshot(
  candidate: unknown,
): asserts candidate is MemoryBankSnapshot {
  if (!candidate || typeof candidate !== "object") {
    throw new Error("MemoryBank snapshot: not an object");
  }
  const s = candidate as Record<string, unknown>;
  if (typeof s.snapshot_id !== "string") {
    throw new Error("MemoryBank snapshot: missing snapshot_id");
  }
  if (!Array.isArray(s.ticks) || s.ticks.length === 0) {
    throw new Error("MemoryBank snapshot: ticks must be a non-empty array");
  }
  for (const t of s.ticks as MemoryBankTick[]) {
    if (typeof t.tick !== "number" || typeof t.dwell_ms !== "number") {
      throw new Error(
        `MemoryBank snapshot: tick missing tick/dwell_ms (got ${JSON.stringify(t)})`,
      );
    }
    if (typeof t.diary !== "string" || t.diary.length === 0) {
      throw new Error(`MemoryBank snapshot: tick ${t.tick} has empty diary`);
    }
  }
}
