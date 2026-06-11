/**
 * wsContract.ts — Track-D-canonical TypeScript mirror of the
 * `dashboard_ws_message.v0.3.0.json` wire schema.
 *
 * v0.3.0 (F0) is a field-additive MINOR bump over v0.2.0: it adds three
 * OPTIONAL fields — `market_id`, `bet_id`, `signals` — to BOTH the
 * decision payload and the decision_feed entry. No new kinds.
 *
 * This file is a **pure type declaration module**; it has no runtime
 * footprint other than the `WS_CONTRACT_VERSION` constant and the
 * `KNOWN_KINDS_V0_2_0` enumeration the WS client uses to whitelist
 * incoming frames.
 *
 * Why a separate file from `types.ts` (which existed in v0.1.0)?
 *   - Track B (Pydantic models in agent/dashboard_bridge/event_emitter.py)
 *     is the SOURCE OF TRUTH for the wire schema. The byte-identical
 *     mirror lives here.
 *   - `types.ts` will be progressively migrated into this module; for
 *     v0.2.0 we keep types.ts as the runtime guard + re-export surface
 *     so existing imports (`@/lib/types`) don't break, and add the
 *     two new kinds + the canonical contract version constant here.
 *   - The Demo-Readiness reviewer's interface_matrix gate checks for
 *     the literal `WS_CONTRACT_VERSION` string against the registry.
 *
 * IMPORTANT: every field below MUST trace to a field in
 * agent/dashboard_bridge/event_emitter.py. If a field is missing
 * upstream, file a `proposed_spec_change` instead of inventing it.
 */

import type {
  AgentPhase,
  CauseOfDeath,
  DecisionPayload,
  VitalsPayload,
  WeightsPayload,
  WsMessageBaseFields,
} from "./types";

/** Canonical version string consumed by the interface registry. */
export const WS_CONTRACT_VERSION = "0.3.0" as const;

/**
 * v0.3.0 — the 5 LOWERCASE persisted engine names that key a `signals`
 * map (mirrors $defs.*.signals.propertyNames in the wire schema). NOT
 * the uppercase display constants, NOT 5 fixed scalars.
 */
export const SIGNAL_ENGINE_KEYS = [
  "tennis_technical",
  "market_momentum",
  "smart_money",
  "sentiment_llm",
  "crowd_volume",
] as const;

export type SignalEngineKey = (typeof SIGNAL_ENGINE_KEYS)[number];

/** Decision-time per-engine score map — `{engine_name: score}`. */
export type EngineSignalMap = Partial<Record<SignalEngineKey, number>>;

/* ------------------------------------------------------------------ */
/* NEW v0.2.0 payload shapes                                          */
/* ------------------------------------------------------------------ */

/** One row of the bounded recent-decisions feed (sprint_4). */
export interface DecisionFeedEntry {
  /**
   * Canonical decision identifier. Either the on-chain sigHash hex
   * (preferred — matches DecisionLog.sol) or the off-chain row pk.
   * Dashboard dedups merges by this key.
   */
  readonly id: string;
  readonly ts: string;
  readonly action: "BET" | "NO_BET";
  readonly side?: string;
  readonly size_usd?: number;
  readonly edge_pct?: number;
  readonly kelly_fraction?: number;
  /** PENDING until the Polymarket settle event is observed. */
  readonly result?: "WIN" | "LOSS" | "PENDING";
  readonly pnl_usd?: number;
  /** LLM reasoning trace (Phase 2+). Optional — Phase 1 BET has none. */
  readonly reasoning?: string;
  /** Post-trade reflection insight, if reflection has run on this row. */
  readonly reflection?: string;
  /** v0.3.0 — Polymarket market id this decision evaluated. */
  readonly market_id?: string;
  /**
   * v0.3.0 — executor-minted uuid == on-chain order_id. The
   * settlement<->decision correlation key (bet_id-keyed). Present on
   * BET rows once an order was placed.
   */
  readonly bet_id?: string;
  /**
   * v0.3.0 — decision-time per-engine score map keyed by the 5
   * lowercase persisted engine names.
   */
  readonly signals?: EngineSignalMap;
}

/** Phase transition payload — drives the PhaseTransitionBanner copy. */
export interface PhaseTransitionPayload {
  readonly from: AgentPhase;
  readonly to: AgentPhase;
  /** Optional human-readable reason ("β₁ unfrozen", "lifeline triggered"). */
  readonly reason?: string;
}

/* ------------------------------------------------------------------ */
/* v0.2.0 messages                                                    */
/* ------------------------------------------------------------------ */

export interface PhaseTransitionMessage extends WsMessageBaseFields {
  readonly kind: "phase_transition";
  readonly payload: PhaseTransitionPayload;
}

export interface DecisionFeedMessage extends WsMessageBaseFields {
  readonly kind: "decision_feed";
  readonly entries: readonly DecisionFeedEntry[];
}

/* ------------------------------------------------------------------ */
/* Whitelist used by the WS client (v0.2.0 superset)                  */
/* ------------------------------------------------------------------ */

/** v0.2.0 kind whitelist. v0.1.0 = the first 10, v0.2.0 adds the last 2. */
export const KNOWN_KINDS_V0_2_0 = [
  "vitals",
  "thought",
  "decision",
  "reflection",
  "weights_updated",
  "llm_activated",
  "desperate_mode_entered",
  "terminal_lucidity_start",
  "last_words",
  "death",
  "phase_transition",
  "decision_feed",
] as const;

export type WsContractKindV0_2_0 = (typeof KNOWN_KINDS_V0_2_0)[number];

/* ------------------------------------------------------------------ */
/* Re-export the v0.1.0 surface so consumers can import everything    */
/* from `@/lib/wsContract` once the migration completes.              */
/* ------------------------------------------------------------------ */
export type {
  AgentPhase,
  CauseOfDeath,
  DecisionPayload,
  VitalsPayload,
  WeightsPayload,
};
