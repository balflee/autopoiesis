"use client";

/**
 * wsStore — Zustand store that holds the latest projected state.
 *
 * The WS client is the WRITER; components are READERS via selectors.
 * Tests inject mock messages by calling `useWsStore.getState().ingest(...)`
 * — no network mocking required. This is the seam the task brief
 * mandates ("tests inject mocks via a Zustand store seam").
 *
 * The store deliberately does NOT hold every frame — only the
 * projection a UI cares about (current vitals, rolling thought stream,
 * latest decision, latest weights, recent decisions feed, weights
 * trajectory for EvolutionCurve).
 *
 * v0.2.0 additions (T-D-003 sprint_4):
 *   - decisionFeed: bounded merged-by-id list (newest first, MAX_FEED rows)
 *   - phaseTransition: latest from→to with a `seenAt` for banner dismissal
 *   - weightsHistory: bounded ring buffer for EvolutionCurve trajectories
 *   - cumulativePnlHistory: derived from decision_feed result settlements
 *   - llmActivatedShown: separate latch so the overlay fires EXACTLY ONCE
 *     even if `llm_activated` is replayed on reconnect
 */

import { create } from "zustand";

import type {
  AgentPhase,
  CauseOfDeath,
  DecisionFeedEntry,
  DecisionPayload,
  PhaseTransitionPayload,
  VitalsPayload,
  WeightsPayload,
  WsMessage,
} from "./types";
import type { WsConnectionState } from "./ws-client";
import type {
  GodsTreasuryRecordData,
  IncarnationLineageEntry,
} from "./sandbox_state_shared";

export interface ThoughtEntry {
  readonly ts: string;
  readonly seq: number;
  readonly text: string;
}

export interface DecisionEntry {
  readonly ts: string;
  readonly seq: number;
  readonly payload: DecisionPayload;
}

export interface WeightsHistoryPoint {
  readonly ts: string;
  readonly seq: number;
  readonly w_r: number;
  readonly w_s: number;
  readonly beta: number;
}

export interface CumulativePnlPoint {
  readonly ts: string;
  readonly cumulative_pnl: number;
  readonly wins: number;
  readonly losses: number;
  /** wins / (wins + losses), 0..1. */
  readonly win_rate: number;
}

export interface PhaseTransitionEntry {
  readonly ts: string;
  readonly seq: number;
  readonly payload: PhaseTransitionPayload;
}

/* ------------------------------------------------------------------ */
/* T-D-004 sprint_5 — Death Watch state slices                         */
/* ------------------------------------------------------------------ */

export interface EnergyThresholdCrossing {
  readonly ts: string;
  readonly seq: number;
  readonly energy_pct: number;
  readonly threshold_pct: number;
  readonly direction: "above" | "below";
}

export interface LastWordsEntry {
  readonly ts: string;
  readonly seq: number;
  readonly text: string;
  readonly tx_hash?: string;
}

export interface TombstoneEntry {
  readonly ts: string;
  readonly seq: number;
  readonly token_id: string;
  readonly ipfs_cid?: string;
  readonly ipfs_degraded: boolean;
  readonly tx_hash?: string;
}

export interface WsState {
  /** null until the first `vitals` frame lands. */
  readonly vitals: VitalsPayload | null;
  /** null until the first `weights_updated` frame lands. */
  readonly weights: WeightsPayload | null;
  /** Bounded rolling buffer — newest last. */
  readonly thoughts: readonly ThoughtEntry[];
  /** Latest decision only — DecisionFeed (below) keeps the full log. */
  readonly latestDecision: DecisionEntry | null;
  /** Latest reflection insight, displayed as the "current learning". */
  readonly latestReflection: string | null;
  /** LLM activation latch — once true, stays true until phase reset. */
  readonly llmActivated: boolean;
  /**
   * One-shot overlay latch. Distinct from `llmActivated` because the
   * overlay must fire EXACTLY ONCE even if `llm_activated` is replayed
   * on a WS reconnect.
   */
  readonly llmActivatedShown: boolean;
  /** Optional note attached to llm_activated frame (v0.2.0). */
  readonly llmActivationNote: string | null;
  /** Desperate-mode entered latch. */
  readonly desperateMode: boolean;
  /** Terminal Lucidity active latch. */
  readonly terminalLucidity: boolean;
  /** Last words (revealed at terminal lucidity end). */
  readonly lastWords: string | null;
  /** Cause of death once `death` frame arrives. */
  readonly causeOfDeath: CauseOfDeath | null;
  /** Connection state — drives the WS badge in VitalsPanel. */
  readonly connection: WsConnectionState;
  /** Monotonic seq of the most recent ingested frame (debug aid). */
  readonly lastSeq: number;

  /* --- v0.2.0 sprint_4 additions --- */

  /** Bounded rolling feed of decisions — newest first, dedup by id. */
  readonly decisionFeed: readonly DecisionFeedEntry[];
  /** Latest phase transition entry, or null. */
  readonly phaseTransition: PhaseTransitionEntry | null;
  /** Bounded ring buffer of weight snapshots for EvolutionCurve. */
  readonly weightsHistory: readonly WeightsHistoryPoint[];
  /** Cumulative win-rate / PnL trajectory derived from settled feed rows. */
  readonly cumulativePnlHistory: readonly CumulativePnlPoint[];

  /* --- T-D-004 sprint_5 — Death Watch slices --- */

  /**
   * Most recent energy threshold crossing. The Death Watch UI triggers
   * when `energyThresholdCrossing.direction === "below"` AND
   * `threshold_pct === 10` (the canonical takeover threshold).
   */
  readonly energyThresholdCrossing: EnergyThresholdCrossing | null;
  /**
   * Sticky latch — true once `terminal_lucidity_entered` lands. Per PRD
   * §6.10 the Terminal flag is sticky: subsequent vitals frames cannot
   * unset this. Death Watch UI consults this to decide whether to keep
   * itself mounted even if energy_pct recovers.
   */
  readonly terminalLucidityEntered: boolean;
  /** BREATH remaining at the moment Terminal Lucidity engaged. */
  readonly terminalBreathAtEntry: number | null;
  /**
   * Full last-words entry — preferred over the v0.2.0 `lastWords` string
   * because it carries the optional `tx_hash` for surfacing the on-chain
   * receipt next to the typewritten text.
   */
  readonly lastWordsEntry: LastWordsEntry | null;
  /** Tombstone mint receipt (includes the ipfs_degraded flag). */
  readonly tombstone: TombstoneEntry | null;

  /** Living Stage P1 — latest divine events (poll's tailed list, newest last). */
  readonly divineEvents: readonly GodsTreasuryRecordData[];
  /** Cumulative gods revenue (successful tributes + cash tithes), USD. */
  readonly divineTreasury: number;
  /** Current incarnation (0 until the Phase 2 supervisor). */
  readonly incarnationNumber: number;
  /** Past-life lineage (folded from deaths.jsonl). */
  readonly reincarnationLineage: readonly IncarnationLineageEntry[];

  /** Bridge from WsClient. */
  ingest: (msg: WsMessage) => void;
  /**
   * Living Stage P1 — poll-path setter for the divine slices. NOT a WsMessage
   * kind: the divine data never rides the socket (it is derived from the
   * /api/sandbox poll bundle), so it stays off the WS-contract surface.
   */
  setDivineState: (p: {
    events: readonly GodsTreasuryRecordData[];
    treasury_usd: number;
    incarnation_number: number;
    lineage: readonly IncarnationLineageEntry[];
  }) => void;
  setConnection: (s: WsConnectionState) => void;
  /** One-shot overlay handshake — components call this when they've
   *  rendered the overlay so it does not refire on reconnect. */
  markLlmOverlayShown: () => void;
  /** Banner dismiss — clears `phaseTransition` so the banner unmounts. */
  dismissPhaseTransition: () => void;
  /** Reset to initial — used by tests. */
  reset: () => void;
}

const MAX_THOUGHTS = 12;
const MAX_FEED = 50;
const MAX_WEIGHTS_HISTORY = 240; // ~4h at 1 frame/min
const MAX_PNL_HISTORY = 240;

const initial: Omit<
  WsState,
  | "ingest"
  | "setConnection"
  | "markLlmOverlayShown"
  | "dismissPhaseTransition"
  | "reset"
  | "setDivineState"
> = {
  vitals: null,
  weights: null,
  thoughts: [],
  latestDecision: null,
  latestReflection: null,
  llmActivated: false,
  llmActivatedShown: false,
  llmActivationNote: null,
  desperateMode: false,
  terminalLucidity: false,
  lastWords: null,
  causeOfDeath: null,
  connection: "idle",
  lastSeq: -1,
  decisionFeed: [],
  phaseTransition: null,
  weightsHistory: [],
  cumulativePnlHistory: [],
  energyThresholdCrossing: null,
  terminalLucidityEntered: false,
  terminalBreathAtEntry: null,
  lastWordsEntry: null,
  tombstone: null,
  divineEvents: [],
  divineTreasury: 0,
  incarnationNumber: 0,
  reincarnationLineage: [],
};

/**
 * Merge a fresh decision_feed payload into the existing feed.
 * Rules:
 *   - id is the canonical dedup key
 *   - newer ts wins on collision (so PENDING → WIN/LOSS settles)
 *   - sort newest-first by ts (ISO-8601 lex compare is correct)
 *   - cap at MAX_FEED
 */
function mergeDecisionFeed(
  prev: readonly DecisionFeedEntry[],
  incoming: readonly DecisionFeedEntry[],
): readonly DecisionFeedEntry[] {
  const byId = new Map<string, DecisionFeedEntry>();
  for (const e of prev) byId.set(e.id, e);
  for (const e of incoming) {
    const existing = byId.get(e.id);
    if (!existing || e.ts >= existing.ts) byId.set(e.id, e);
  }
  const merged = Array.from(byId.values()).sort((a, b) =>
    a.ts < b.ts ? 1 : a.ts > b.ts ? -1 : 0,
  );
  return merged.slice(0, MAX_FEED);
}

/**
 * Derive cumulative win-rate + PnL trajectory from a sorted feed.
 * Only settled rows (WIN/LOSS) contribute. The result is oldest→newest.
 */
function deriveCumulativeHistory(
  feed: readonly DecisionFeedEntry[],
): readonly CumulativePnlPoint[] {
  // Walk oldest→newest so the cumulative makes sense.
  const oldestFirst = [...feed].sort((a, b) =>
    a.ts < b.ts ? -1 : a.ts > b.ts ? 1 : 0,
  );
  let wins = 0;
  let losses = 0;
  let pnl = 0;
  const out: CumulativePnlPoint[] = [];
  for (const row of oldestFirst) {
    if (row.result === "WIN") {
      wins += 1;
      pnl += row.pnl_usd ?? 0;
    } else if (row.result === "LOSS") {
      losses += 1;
      pnl += row.pnl_usd ?? 0;
    } else {
      continue; // PENDING / undefined — not part of the cumulative
    }
    const total = wins + losses;
    out.push({
      ts: row.ts,
      cumulative_pnl: pnl,
      wins,
      losses,
      win_rate: total === 0 ? 0 : wins / total,
    });
  }
  return out.slice(-MAX_PNL_HISTORY);
}

export const useWsStore = create<WsState>((set) => ({
  ...initial,
  ingest: (msg: WsMessage) =>
    set((prev) => {
      const base = { lastSeq: Math.max(prev.lastSeq, msg.seq) };
      switch (msg.kind) {
        case "vitals":
          return { ...base, vitals: msg.payload };
        case "thought": {
          const next = [
            ...prev.thoughts,
            { ts: msg.ts, seq: msg.seq, text: msg.text },
          ];
          if (next.length > MAX_THOUGHTS) next.splice(0, next.length - MAX_THOUGHTS);
          return { ...base, thoughts: next };
        }
        case "decision":
          return {
            ...base,
            latestDecision: { ts: msg.ts, seq: msg.seq, payload: msg.payload },
          };
        case "reflection":
          return { ...base, latestReflection: msg.insight };
        case "weights_updated": {
          const point: WeightsHistoryPoint = {
            ts: msg.ts,
            seq: msg.seq,
            w_r: msg.weights.w_r,
            w_s: msg.weights.w_s,
            beta: msg.weights.beta,
          };
          const nextHistory = [...prev.weightsHistory, point];
          if (nextHistory.length > MAX_WEIGHTS_HISTORY) {
            nextHistory.splice(0, nextHistory.length - MAX_WEIGHTS_HISTORY);
          }
          return {
            ...base,
            weights: msg.weights,
            weightsHistory: nextHistory,
          };
        }
        case "llm_activated":
          // Only flip the "shown" latch the FIRST time. Replay-safe.
          return {
            ...base,
            llmActivated: true,
            llmActivationNote: msg.note ?? prev.llmActivationNote,
          };
        case "desperate_mode_entered":
          return { ...base, desperateMode: true };
        case "terminal_lucidity_start":
          return { ...base, terminalLucidity: true };
        case "last_words":
          return { ...base, lastWords: msg.text };
        case "death":
          return { ...base, causeOfDeath: msg.cause };
        case "phase_transition":
          return {
            ...base,
            phaseTransition: {
              ts: msg.ts,
              seq: msg.seq,
              payload: msg.payload,
            },
          };
        case "decision_feed": {
          const merged = mergeDecisionFeed(prev.decisionFeed, msg.entries);
          return {
            ...base,
            decisionFeed: merged,
            cumulativePnlHistory: deriveCumulativeHistory(merged),
          };
        }
        case "energy_threshold_crossed":
          return {
            ...base,
            energyThresholdCrossing: {
              ts: msg.ts,
              seq: msg.seq,
              energy_pct: msg.energy_pct,
              threshold_pct: msg.threshold_pct,
              direction: msg.direction,
            },
          };
        case "terminal_lucidity_entered":
          // Sticky latch — once true, never reset by the ingest path.
          // The only way to clear is via `reset()` (used only in tests).
          return {
            ...base,
            terminalLucidity: true,
            terminalLucidityEntered: true,
            terminalBreathAtEntry: msg.breath_at_entry,
          };
        case "last_words_emitted":
          return {
            ...base,
            // Mirror into the v0.2.0 lastWords string so older consumers
            // keep working — but the canonical entry carries tx_hash.
            lastWords: msg.text,
            lastWordsEntry: {
              ts: msg.ts,
              seq: msg.seq,
              text: msg.text,
              tx_hash: msg.tx_hash,
            },
          };
        case "tombstone_minted":
          return {
            ...base,
            tombstone: {
              ts: msg.ts,
              seq: msg.seq,
              token_id: msg.token_id,
              ipfs_cid: msg.ipfs_cid,
              ipfs_degraded: msg.ipfs_degraded,
              tx_hash: msg.tx_hash,
            },
          };
        default: {
          const _exhaustive: never = msg;
          void _exhaustive;
          return base;
        }
      }
    }),
  setConnection: (s) => set({ connection: s }),
  setDivineState: (p) =>
    set({
      divineEvents: p.events,
      divineTreasury: p.treasury_usd,
      incarnationNumber: p.incarnation_number,
      reincarnationLineage: p.lineage,
    }),
  markLlmOverlayShown: () => set({ llmActivatedShown: true }),
  dismissPhaseTransition: () => set({ phaseTransition: null }),
  reset: () => set({ ...initial }),
}));

/** Convenience selectors — kept here so components share one shape. */
export const selectVitals = (s: WsState): VitalsPayload | null => s.vitals;
export const selectWeights = (s: WsState): WeightsPayload | null => s.weights;
export const selectPhase = (s: WsState): AgentPhase | null =>
  s.vitals?.phase ?? null;
export const selectDecisionFeed = (s: WsState): readonly DecisionFeedEntry[] =>
  s.decisionFeed;
export const selectWeightsHistory = (
  s: WsState,
): readonly WeightsHistoryPoint[] => s.weightsHistory;
export const selectCumulativePnl = (
  s: WsState,
): readonly CumulativePnlPoint[] => s.cumulativePnlHistory;

/* --- Living Stage P1 — divine economy selectors --- */
export const selectDivineEvents = (
  s: WsState,
): readonly GodsTreasuryRecordData[] => s.divineEvents;
export const selectDivineTreasury = (s: WsState): number => s.divineTreasury;
export const selectIncarnationNumber = (s: WsState): number => s.incarnationNumber;
export const selectReincarnationLineage = (
  s: WsState,
): readonly IncarnationLineageEntry[] => s.reincarnationLineage;

/* --- T-D-004 sprint_5 — Death Watch selectors --- */

/** True iff the Death Watch UI should currently be visible.
 *  - Triggers on energy_threshold_crossed (direction=below, threshold=10).
 *  - Stays visible once terminalLucidityEntered latches (PRD §6.10 sticky).
 *  - Also surfaces in degenerate fallback when terminalLucidity (legacy
 *    v0.2.0 latch) flips true — keeps backward compat with producers that
 *    still emit `terminal_lucidity_start` instead of the v0.1.0 entered.
 */
export const selectDeathWatchVisible = (s: WsState): boolean => {
  if (s.terminalLucidityEntered) return true;
  if (s.terminalLucidity) return true;
  const x = s.energyThresholdCrossing;
  if (
    x &&
    x.direction === "below" &&
    x.threshold_pct === 10 &&
    x.energy_pct <= x.threshold_pct
  ) {
    return true;
  }
  // Fallback: vitals projection — if the backend never emits the
  // crossing event but vitals show energy ≤10, still trigger.
  if (s.vitals && s.vitals.breath <= 10) return true;
  return false;
};
export const selectTerminalLucidityEntered = (s: WsState): boolean =>
  s.terminalLucidityEntered;
export const selectLastWordsEntry = (s: WsState): LastWordsEntry | null =>
  s.lastWordsEntry;
export const selectTombstone = (s: WsState): TombstoneEntry | null =>
  s.tombstone;
