/**
 * Dashboard WebSocket Message union — TECHNICAL_PLAN.md §5.4.
 *
 * Track B is the PRODUCER (agent runtime emits frames over the WS
 * bridge); Track D consumes them. The wire schema mirror lives at
 * `.dev/contracts/dashboard_ws_message.v0.3.0.json` and is what the
 * registry checks during interface_contract_gate.
 *
 * v0.3.0 (F0) — field-additive MINOR bump from v0.2.0:
 *   - adds OPTIONAL `market_id` / `bet_id` / `signals` to BOTH
 *     `DecisionPayload` and `DecisionFeedEntry`. `bet_id` is the
 *     executor-minted uuid (== on-chain order_id) used to correlate a
 *     settlement back to its decision; `signals` is a name->score map
 *     keyed by the 5 lowercase persisted engine names.
 *   - no new kinds; the 12 v0.2.0 kinds are byte-identical, so every
 *     v0.2.0 frame still validates.
 *
 * v0.2.0 (T-D-003) — MINOR bump from v0.1.0:
 *   - adds `phase_transition` kind (PhaseTransitionPayload)
 *   - adds `decision_feed` kind (bounded DecisionFeedEntry[])
 *   - extends `llm_activated` with optional `note` string
 *   - existing 10 kinds remain byte-identical so v0.1.0 producers
 *     still satisfy the schema
 *
 * The 12 kinds covered by the v0.2.0 union are:
 *   vitals | thought | decision | reflection | weights_updated
 *   | llm_activated | desperate_mode_entered | terminal_lucidity_start
 *   | last_words | death | phase_transition | decision_feed
 *
 * Adding a new kind without bumping the schema version + the registry
 * is a contract break and will fail interface_matrix_updated.
 */

/** Lifecycle phase the Agent is currently in (PRD §5). */
export type AgentPhase =
  | "PHASE_1_INFANCY"
  | "PHASE_2_APPRENTICE"
  | "PHASE_3_MASTER"
  | "PHASE_4_TERMINAL";

/** Cause-of-death taxonomy mirrored from AgentLifecycle. */
export type CauseOfDeath =
  | "ENERGY_DEPLETED"
  | "TERMINAL_LUCIDITY_COMPLETED"
  | "FORCED";

export interface VitalsPayload {
  /** Remaining BREATH energy (PRD §4 — soft cap 100, depletes over time). */
  readonly breath: number;
  /** Polymarket bankroll, USD. */
  readonly bankroll: number;
  /** Seconds until next phase boundary / next decision tick. */
  readonly countdown_s: number;
  /** Burn rate per minute (used by the VitalsPanel countdown ribbon). */
  readonly gas_per_min: number;
  /** Current phase enum. */
  readonly phase: AgentPhase;
}

/** Dual-engine weight tuple (mirrors MemoryBank.Weights). */
export interface WeightsPayload {
  readonly w_r: number;
  readonly w_s: number;
  readonly alpha: number;
  readonly beta: number;
  readonly rho: number;
}

/**
 * v0.3.0 — the 5 LOWERCASE persisted engine names that may key a
 * `signals` map (mirrors $defs.*.signals.propertyNames in the wire
 * schema). NOT the uppercase display constants, NOT 5 fixed scalars.
 */
export type SignalEngineKey =
  | "tennis_technical"
  | "market_momentum"
  | "smart_money"
  | "sentiment_llm"
  | "crowd_volume";

/** v0.3.0 — decision-time per-engine score map (`{engine_name: score}`). */
export type EngineSignalMap = Partial<Record<SignalEngineKey, number>>;

export interface DecisionPayload {
  readonly action: "BET" | "NO_BET";
  readonly side?: string;
  readonly size_usd?: number;
  readonly edge_pct?: number;
  readonly kelly_fraction?: number;
  /** v0.3.0 — Polymarket market id this decision evaluated. */
  readonly market_id?: string;
  /**
   * v0.3.0 — executor-minted uuid == on-chain order_id. The
   * settlement<->decision correlation key (bet_id-keyed).
   */
  readonly bet_id?: string;
  /** v0.3.0 — per-engine score map keyed by the 5 lowercase engine names. */
  readonly signals?: EngineSignalMap;
}

/* ------------------------------------------------------------------ */
/* v0.2.0 new payload shapes                                          */
/* ------------------------------------------------------------------ */

/** Recent-decisions feed row — see wsContract.ts for canonical doc. */
export interface DecisionFeedEntry {
  readonly id: string;
  readonly ts: string;
  readonly action: "BET" | "NO_BET";
  readonly side?: string;
  readonly size_usd?: number;
  readonly edge_pct?: number;
  readonly kelly_fraction?: number;
  readonly result?: "WIN" | "LOSS" | "PENDING";
  readonly pnl_usd?: number;
  readonly reasoning?: string;
  readonly reflection?: string;
  /** v0.3.0 — Polymarket market id this decision evaluated. */
  readonly market_id?: string;
  /** v0.3.0 — executor-minted uuid == on-chain order_id (correlation key). */
  readonly bet_id?: string;
  /** v0.3.0 — per-engine score map keyed by the 5 lowercase engine names. */
  readonly signals?: EngineSignalMap;
}

export interface PhaseTransitionPayload {
  readonly from: AgentPhase;
  readonly to: AgentPhase;
  readonly reason?: string;
}

/* ------------------------------------------------------------------ */
/* Discriminated union — kind is the discriminant                     */
/* ------------------------------------------------------------------ */

/**
 * Common envelope shared by every WS frame. Exported so wsContract.ts
 * can extend without duplicating the `ts` + `seq` boilerplate.
 */
export interface WsMessageBaseFields {
  /** ISO-8601 timestamp from the producer. */
  readonly ts: string;
  /** Monotonic sequence number — Dashboard uses this to dedup + order. */
  readonly seq: number;
}

/** Legacy alias retained for v0.1.0 callers. */
type WsMessageBase = WsMessageBaseFields;

export interface VitalsMessage extends WsMessageBase {
  readonly kind: "vitals";
  readonly payload: VitalsPayload;
}

export interface ThoughtMessage extends WsMessageBase {
  readonly kind: "thought";
  readonly text: string;
}

export interface DecisionMessage extends WsMessageBase {
  readonly kind: "decision";
  readonly payload: DecisionPayload;
}

export interface ReflectionMessage extends WsMessageBase {
  readonly kind: "reflection";
  readonly insight: string;
}

export interface WeightsUpdatedMessage extends WsMessageBase {
  readonly kind: "weights_updated";
  readonly weights: WeightsPayload;
}

export interface LlmActivatedMessage extends WsMessageBase {
  readonly kind: "llm_activated";
  /** v0.2.0 — optional, e.g. "β₁ unfrozen at Phase 2 boundary". */
  readonly note?: string;
}

export interface DesperateModeEnteredMessage extends WsMessageBase {
  readonly kind: "desperate_mode_entered";
}

export interface TerminalLucidityStartMessage extends WsMessageBase {
  readonly kind: "terminal_lucidity_start";
}

export interface LastWordsMessage extends WsMessageBase {
  readonly kind: "last_words";
  readonly text: string;
}

export interface DeathMessage extends WsMessageBase {
  readonly kind: "death";
  readonly cause: CauseOfDeath;
}

/* v0.2.0 new messages */

export interface PhaseTransitionMessage extends WsMessageBase {
  readonly kind: "phase_transition";
  readonly payload: PhaseTransitionPayload;
}

export interface DecisionFeedMessage extends WsMessageBase {
  readonly kind: "decision_feed";
  readonly entries: readonly DecisionFeedEntry[];
}

/* ------------------------------------------------------------------ */
/* T-D-004 sprint_5 — death-watch event kinds (parallel contract)     */
/*                                                                    */
/* These four kinds live in `dashboard_death_watch.v0.1.0.json` and    */
/* are mirrored in `wsEvents.ts` (payload + type guards). They are     */
/* added to the union + KNOWN_KINDS below so the WS client whitelist   */
/* accepts them; a future v0.3.0 main-schema bump will fold them in.   */
/* ------------------------------------------------------------------ */

export interface EnergyThresholdCrossedMessage extends WsMessageBase {
  readonly kind: "energy_threshold_crossed";
  readonly energy_pct: number;
  readonly threshold_pct: number;
  readonly direction: "above" | "below";
}

export interface TerminalLucidityEnteredMessage extends WsMessageBase {
  readonly kind: "terminal_lucidity_entered";
  readonly breath_at_entry: number;
}

export interface LastWordsEmittedMessage extends WsMessageBase {
  readonly kind: "last_words_emitted";
  readonly text: string;
  readonly tx_hash?: string;
}

export interface TombstoneMintedMessage extends WsMessageBase {
  readonly kind: "tombstone_minted";
  readonly token_id: string;
  readonly ipfs_cid?: string;
  readonly ipfs_degraded: boolean;
  readonly tx_hash?: string;
}

/**
 * THE union. TypeScript narrows on `kind` for exhaustive switches.
 * If you add a new kind here you MUST also:
 *   1. Bump dashboard_ws_message.v*.json + _registry.json
 *   2. Update the wsClient sniffer's known-kinds list (KNOWN_KINDS below)
 *   3. Update wsContract.ts KNOWN_KINDS_V0_x_y enumeration
 *   4. Add a test case in __tests__/lib/ws-client.test.ts
 *   5. Add a switch case in wsStore.ts ingest()
 */
export type WsMessage =
  | VitalsMessage
  | ThoughtMessage
  | DecisionMessage
  | ReflectionMessage
  | WeightsUpdatedMessage
  | LlmActivatedMessage
  | DesperateModeEnteredMessage
  | TerminalLucidityStartMessage
  | LastWordsMessage
  | DeathMessage
  | PhaseTransitionMessage
  | DecisionFeedMessage
  | EnergyThresholdCrossedMessage
  | TerminalLucidityEnteredMessage
  | LastWordsEmittedMessage
  | TombstoneMintedMessage;

export type WsMessageKind = WsMessage["kind"];

export const KNOWN_KINDS: readonly WsMessageKind[] = [
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
  "energy_threshold_crossed",
  "terminal_lucidity_entered",
  "last_words_emitted",
  "tombstone_minted",
] as const;

/**
 * Narrow runtime guard — cheap, no zod. Returns true if `candidate`
 * looks like a well-formed WsMessage; the WS client uses it to drop
 * garbage frames before they reach the store.
 */
export function isWsMessage(candidate: unknown): candidate is WsMessage {
  if (!candidate || typeof candidate !== "object") return false;
  const m = candidate as Record<string, unknown>;
  if (typeof m.kind !== "string") return false;
  if (typeof m.ts !== "string") return false;
  if (typeof m.seq !== "number") return false;
  return (KNOWN_KINDS as readonly string[]).includes(m.kind);
}
