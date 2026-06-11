/**
 * wsEvents.ts — TypeScript mirror of `dashboard_death_watch.v0.1.0.json`.
 *
 * Track D consumes four NEW WebSocket events that drive the Demo §9
 * 4:00-5:00 climax (Death Watch surface):
 *
 *   - energy_threshold_crossed   → triggers DeathWatch takeover at 10%
 *   - terminal_lucidity_entered  → latches the sticky terminal flag (PRD §6.10)
 *   - last_words_emitted         → drives LastWordsTypewriter letter-by-letter
 *   - tombstone_minted           → drives TombstoneMintAnimation + ipfs_degraded badge
 *
 * Why a separate module from `types.ts`?
 *   - These four kinds are owned by a focused producer worktree (T-B-010
 *     dashboard_bridge) and live in their own schema file so producer +
 *     consumer can converge ahead of bumping the main `dashboard_ws_message`
 *     contract to v0.3.0.
 *   - Keeping the type guards co-located with the payload shapes makes it
 *     easier for the producer's golden-file test (T-B-010) to import them.
 *   - `types.ts` re-exports the union + extends KNOWN_KINDS so the WS
 *     client whitelist accepts these frames.
 *
 * IMPORTANT: every field below MUST trace to a field declared in
 * `dashboard_death_watch.v0.1.0.json`. If a producer field is missing
 * here, file a `proposed_spec_change` in your delivery report — DO NOT
 * invent fields client-side.
 */

import type { WsMessageBaseFields } from "./types";

/** Canonical version string consumed by the interface registry. */
export const DEATH_WATCH_CONTRACT_VERSION = "0.1.0" as const;

/* ------------------------------------------------------------------ */
/* Payload shapes                                                      */
/* ------------------------------------------------------------------ */

/**
 * Energy threshold crossing. Track D listens for
 * `direction === "below" && threshold_pct === 10` as the canonical
 * Death Watch takeover trigger.
 */
export interface EnergyThresholdCrossedPayload {
  readonly energy_pct: number;
  readonly threshold_pct: number;
  readonly direction: "above" | "below";
}

/**
 * Terminal Lucidity commit. Distinct from the v0.2.0 `terminal_lucidity_start`
 * heads-up; this event LATCHES the sticky Phase 4 flag — Death Watch UI
 * cannot be dismissed even if energy_pct climbs back above 10 (PRD §6.10).
 */
export interface TerminalLucidityEnteredPayload {
  readonly breath_at_entry: number;
}

/**
 * Agent's terminal `dieWithLastWords()` text. `tx_hash` is optional —
 * the producer may emit the text before the txn confirms.
 */
export interface LastWordsEmittedPayload {
  readonly text: string;
  readonly tx_hash?: string;
}

/**
 * TombstoneNFT mint confirmation. `ipfs_degraded === true` ⇒ the
 * memory-bank IPFS pin failed (PRD §5.1.C); the UI surfaces a
 * 'memory bank pin failed — text-only tombstone' badge. `ipfs_cid` is
 * undefined exactly in that case.
 */
export interface TombstoneMintedPayload {
  readonly token_id: string;
  readonly ipfs_cid?: string;
  readonly ipfs_degraded: boolean;
  readonly tx_hash?: string;
}

/* ------------------------------------------------------------------ */
/* Messages                                                            */
/* ------------------------------------------------------------------ */

export interface EnergyThresholdCrossedMessage extends WsMessageBaseFields {
  readonly kind: "energy_threshold_crossed";
  readonly energy_pct: number;
  readonly threshold_pct: number;
  readonly direction: "above" | "below";
}

export interface TerminalLucidityEnteredMessage extends WsMessageBaseFields {
  readonly kind: "terminal_lucidity_entered";
  readonly breath_at_entry: number;
}

export interface LastWordsEmittedMessage extends WsMessageBaseFields {
  readonly kind: "last_words_emitted";
  readonly text: string;
  readonly tx_hash?: string;
}

export interface TombstoneMintedMessage extends WsMessageBaseFields {
  readonly kind: "tombstone_minted";
  readonly token_id: string;
  readonly ipfs_cid?: string;
  readonly ipfs_degraded: boolean;
  readonly tx_hash?: string;
}

export type DeathWatchMessage =
  | EnergyThresholdCrossedMessage
  | TerminalLucidityEnteredMessage
  | LastWordsEmittedMessage
  | TombstoneMintedMessage;

export type DeathWatchKind = DeathWatchMessage["kind"];

export const DEATH_WATCH_KINDS = [
  "energy_threshold_crossed",
  "terminal_lucidity_entered",
  "last_words_emitted",
  "tombstone_minted",
] as const satisfies readonly DeathWatchKind[];

/* ------------------------------------------------------------------ */
/* Type guards                                                         */
/* ------------------------------------------------------------------ */

function hasBaseEnvelope(
  candidate: unknown,
): candidate is { kind: string } & WsMessageBaseFields {
  if (!candidate || typeof candidate !== "object") return false;
  const m = candidate as unknown as Record<string, unknown>;
  return (
    typeof m.kind === "string" &&
    typeof m.ts === "string" &&
    typeof m.seq === "number"
  );
}

const HEX_TX_HASH_RE = /^0x[0-9a-fA-F]{64}$/;

export function isEnergyThresholdCrossed(
  candidate: unknown,
): candidate is EnergyThresholdCrossedMessage {
  if (!hasBaseEnvelope(candidate)) return false;
  const m = candidate as unknown as Record<string, unknown>;
  if (m.kind !== "energy_threshold_crossed") return false;
  if (typeof m.energy_pct !== "number") return false;
  if (typeof m.threshold_pct !== "number") return false;
  if (m.direction !== "above" && m.direction !== "below") return false;
  if (m.energy_pct < 0 || m.energy_pct > 100) return false;
  if (m.threshold_pct < 0 || m.threshold_pct > 100) return false;
  return true;
}

export function isTerminalLucidityEntered(
  candidate: unknown,
): candidate is TerminalLucidityEnteredMessage {
  if (!hasBaseEnvelope(candidate)) return false;
  const m = candidate as unknown as Record<string, unknown>;
  if (m.kind !== "terminal_lucidity_entered") return false;
  if (typeof m.breath_at_entry !== "number") return false;
  if (m.breath_at_entry < 0) return false;
  return true;
}

export function isLastWordsEmitted(
  candidate: unknown,
): candidate is LastWordsEmittedMessage {
  if (!hasBaseEnvelope(candidate)) return false;
  const m = candidate as unknown as Record<string, unknown>;
  if (m.kind !== "last_words_emitted") return false;
  if (typeof m.text !== "string" || m.text.length === 0) return false;
  if (m.text.length > 1024) return false;
  if (m.tx_hash !== undefined) {
    if (typeof m.tx_hash !== "string") return false;
    if (!HEX_TX_HASH_RE.test(m.tx_hash)) return false;
  }
  return true;
}

export function isTombstoneMinted(
  candidate: unknown,
): candidate is TombstoneMintedMessage {
  if (!hasBaseEnvelope(candidate)) return false;
  const m = candidate as unknown as Record<string, unknown>;
  if (m.kind !== "tombstone_minted") return false;
  if (typeof m.token_id !== "string" || m.token_id.length === 0) return false;
  if (typeof m.ipfs_degraded !== "boolean") return false;
  if (m.ipfs_cid !== undefined && typeof m.ipfs_cid !== "string") return false;
  // Schema invariant: ipfs_cid undefined iff ipfs_degraded === true.
  // (We do NOT hard-reject because the producer may still emit a CID when
  // ipfs_degraded === true — the UI just renders the degraded badge.)
  if (m.tx_hash !== undefined) {
    if (typeof m.tx_hash !== "string") return false;
    if (!HEX_TX_HASH_RE.test(m.tx_hash)) return false;
  }
  return true;
}

/**
 * Combined guard — true for any of the four death-watch events. Used by
 * the WS client when a frame's kind matches a death-watch kind but the
 * payload shape needs verification before it reaches the store.
 */
export function isDeathWatchMessage(
  candidate: unknown,
): candidate is DeathWatchMessage {
  return (
    isEnergyThresholdCrossed(candidate) ||
    isTerminalLucidityEntered(candidate) ||
    isLastWordsEmitted(candidate) ||
    isTombstoneMinted(candidate)
  );
}
