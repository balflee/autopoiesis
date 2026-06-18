/**
 * dashboard/lib/sandbox_state_shared.ts — shared helpers for sprint_8 T-D-009.
 *
 * **No `"use client"` directive** — this module is imported by BOTH
 * the client hook (`load_sandbox_state.ts`) and the server loader
 * (`load_sandbox_state.server.ts`). Splitting the pure helpers out
 * of the client module fixes a Next.js 15 boundary error:
 *
 *     Attempted to call computeLagAlerts() from the server but
 *     computeLagAlerts is on the client.
 *
 * Background: a file with `"use client"` wraps every export so it can
 * cross the server↔client boundary as a reference, not a value. The
 * API route runs on the server and CALLS computeLagAlerts as a value
 * — so the helpers must live in a non-"use client" module.
 *
 * All exports here are PURE: same inputs → same outputs, no React,
 * no Node fs, no window access. They compile into both server and
 * client bundles cleanly.
 */

import type { AgentPhase, DecisionFeedEntry, EngineSignalMap } from "@/lib/types";

/* ------------------------------------------------------------------ */
/* Wire shapes — mirror agent.data.sandbox_state.* Pydantic models     */
/* ------------------------------------------------------------------ */

/** Mirror of :class:`agent.data.sandbox_state.AgentStateSnapshot`. */
export interface AgentStateSnapshotData {
  readonly snapshot_ts: string;
  readonly phase: AgentPhase;
  readonly breath: number;
  readonly bankroll_usd: number;
  readonly phase_age_days: number;
  readonly open_bet_ids: readonly string[];
  readonly last_tick: number;
  /** Living Stage P1 — incarnation index (0 until the Phase 2 supervisor). */
  readonly incarnation_number?: number;
  /** Optional fusion-weights blob (T-B-020 multi-day rehydrate). */
  readonly weights: WeightsSnapshotData | null;
  /** Sticky desperate-mode latch (PRD §6.5). */
  readonly desperate: boolean;
}

/** 6-parameter fusion weight snapshot — mirrors agent.core.state.Weights. */
export interface WeightsSnapshotData {
  readonly w_r: number;
  readonly w_s: number;
  readonly alpha_1?: number;
  readonly alpha_2?: number;
  readonly alpha_3?: number;
  readonly beta_1?: number;
  readonly beta_2?: number;
  readonly rho?: number;
}

/** Mirror of :class:`agent.data.sandbox_state.DecisionRecord`. */
export interface DecisionRecordData {
  readonly tick: number;
  readonly ts: string;
  readonly market_id: string | null;
  readonly kind: "BET" | "NO_BET";
  readonly size_usd: number;
  readonly side: "YES" | "NO" | null;
  readonly edge_pct: number | null;
  readonly no_bet_reason: string | null;
  readonly breath_after: number;
  readonly bankroll_usd_after: number;
  /** Living Stage P1 — present only on live divine-economy decision ticks. */
  readonly odds_yes?: number;
  readonly odds_no?: number;
  readonly fee_floor_pct?: number;
  readonly signal_scores?: Record<string, number>;
}

/** Mirror of :class:`agent.data.sandbox_state.SettledBetRecord`. */
export interface SettledBetRecordData {
  readonly bet_id: string;
  readonly market_id: string;
  readonly settled_ts: string;
  readonly outcome: "yes" | "no" | "void";
  readonly winning_price: number;
  readonly pnl_usd: number;
  readonly status: "settled";
}

/** Living Stage P1 — one tribute offering row in gods_treasury.jsonl. */
export interface TributeRecordData {
  readonly type: "tribute";
  readonly tribute_id: string;
  readonly ts: string;
  readonly tick: number;
  readonly amount_usd: number;
  readonly success: boolean;
  readonly breath_after: number;
  readonly bankroll_after: number;
  readonly dice_roll?: number;
}

/** Living Stage P1 — one tithe (divine rent) row in gods_treasury.jsonl. */
export interface TitheRecordData {
  readonly type: "tithe";
  readonly tithe_id: string;
  readonly ts: string;
  readonly tick: number;
  readonly paid_usd: number;
  readonly breath_cost: number;
  readonly breath_after: number;
  readonly bankroll_after: number;
}

/** Living Stage P1 — interleaved treasury stream row (discriminated by `type`). */
export type GodsTreasuryRecordData = TributeRecordData | TitheRecordData;

/** Living Stage P1 — one past-life summary, folded from deaths.jsonl. */
export interface IncarnationLineageEntry {
  readonly incarnation_number: number;
  readonly last_tick: number;
  readonly cause: string;
  readonly final_bankroll_usd: number;
  readonly ts: string;
}

/**
 * One advisory about a sandbox-state staleness / cold-boot condition.
 *
 * The UI banner consumes `severity` to colour-tint; `kind` is a stable
 * string the test harness can grep for.
 */
export interface LagAlert {
  readonly kind:
    | "cold_boot"
    | "snapshot_stale"
    | "missing_snapshot"
    | "fs_error";
  readonly detail: string;
  readonly severity: "info" | "warn" | "error";
}

/** Bundle the loader returns + the hook surfaces. */
export interface SandboxStateBundle {
  readonly snapshot: AgentStateSnapshotData | null;
  readonly recent_decisions: readonly DecisionRecordData[];
  readonly recent_settled: readonly SettledBetRecordData[];
  readonly lag_alerts: readonly LagAlert[];
  /** Wall-clock when the route assembled this bundle. */
  readonly served_ts: string;
  /** True iff the API route served from the in-memory mock. */
  readonly is_mock: boolean;
  // Living Stage P1 — divine economy (poll-derived).
  readonly recent_gods_treasury: readonly GodsTreasuryRecordData[];
  readonly gods_revenue_cumulative_usd: number;
  readonly incarnation_number: number;
  readonly incarnation_lineage: readonly IncarnationLineageEntry[];
}

/* ------------------------------------------------------------------ */
/* Defaults & env overrides                                            */
/* ------------------------------------------------------------------ */

/** Brief acceptance: poll every 2 s. */
export const DEFAULT_POLL_MS = 2_000;

/** `INITIAL_BREATH` denominator for the Death Watch ratio. */
export const DEFAULT_INITIAL_BREATH = 100;

/** Brief acceptance: tail N=50. */
export const DEFAULT_TAIL_N = 50;

/** Stale threshold for snapshot ts → triggers a lag alert. */
export const SNAPSHOT_STALE_MS = 30_000;

/** "below threshold" → Death Watch full-screen takeover. */
export const DEATH_WATCH_RATIO = 0.10;

/** Resolve the active INITIAL_BREATH denominator (window > env > default). */
export function readInitialBreath(): number {
  if (typeof window !== "undefined") {
    const w = window as unknown as {
      __GENESIS_INITIAL_BREATH__?: number | string;
    };
    const raw = w.__GENESIS_INITIAL_BREATH__;
    const parsed = typeof raw === "string" ? Number(raw) : raw;
    if (typeof parsed === "number" && Number.isFinite(parsed) && parsed > 0) {
      return parsed;
    }
  }
  const env = process.env.NEXT_PUBLIC_INITIAL_BREATH;
  if (typeof env === "string" && env.length > 0) {
    const n = Number(env);
    if (Number.isFinite(n) && n > 0) return n;
  }
  return DEFAULT_INITIAL_BREATH;
}

/** Resolve the poll cadence — env override; defaults to 2 s. */
export function readPollIntervalMs(): number {
  const env = process.env.NEXT_PUBLIC_SANDBOX_POLL_MS;
  if (typeof env === "string" && env.length > 0) {
    const n = Number(env);
    if (Number.isFinite(n) && n >= 100) return n;
  }
  return DEFAULT_POLL_MS;
}

/* ------------------------------------------------------------------ */
/* Pure helpers — used by both the server loader and the test suite    */
/* ------------------------------------------------------------------ */

/**
 * Pure helper — parse a JSONL string into objects, skipping bad lines.
 *
 * Track B's writer guarantees one JSON object per line via POSIX
 * append-atomicity (see :mod:`agent.data.sandbox_state` docstring).
 * Bad lines (e.g. a truncated tail during a race) are skipped, NOT
 * raised — the dashboard cannot afford to crash on a single torn
 * write.
 */
export function parseJsonl<T>(text: string): T[] {
  if (!text) return [];
  const out: T[] = [];
  for (const raw of text.split(/\r?\n/)) {
    const stripped = raw.trim();
    if (!stripped) continue;
    try {
      const obj = JSON.parse(stripped) as unknown;
      if (obj !== null && typeof obj === "object") out.push(obj as T);
    } catch {
      /* skip torn line — defensive against tail-race */
    }
  }
  return out;
}

/**
 * Take the last N elements of an array (newest assumed last per the
 * writer convention). Returned in oldest→newest order to preserve
 * `tick` monotonicity.
 */
export function lastN<T>(arr: readonly T[], n: number): T[] {
  if (n <= 0 || arr.length === 0) return [];
  const start = Math.max(0, arr.length - n);
  return arr.slice(start);
}

/**
 * Compute the lag-alerts for a freshly-loaded bundle.
 *
 * - `cold_boot`       — directory does not exist yet (writer not booted)
 * - `missing_snapshot` — directory exists but snapshot is absent
 * - `snapshot_stale`  — snapshot ts is older than `SNAPSHOT_STALE_MS`
 * - `fs_error`        — added by the route on `try/catch` perimeter
 */
export function computeLagAlerts(
  snapshot: AgentStateSnapshotData | null,
  now: number,
  dirExists: boolean,
): LagAlert[] {
  const alerts: LagAlert[] = [];
  if (!dirExists) {
    alerts.push({
      kind: "cold_boot",
      detail: "state/sandbox/ directory not yet created by Track B runtime",
      severity: "info",
    });
    return alerts;
  }
  if (snapshot == null) {
    alerts.push({
      kind: "missing_snapshot",
      detail: "agent_state.json not yet written",
      severity: "info",
    });
    return alerts;
  }
  const snapTs = Date.parse(snapshot.snapshot_ts);
  if (Number.isFinite(snapTs) && now - snapTs > SNAPSHOT_STALE_MS) {
    const ageS = Math.floor((now - snapTs) / 1_000);
    alerts.push({
      kind: "snapshot_stale",
      detail: `snapshot is ${ageS}s old (> ${SNAPSHOT_STALE_MS / 1_000}s threshold)`,
      severity: "warn",
    });
  }
  return alerts;
}

/* ------------------------------------------------------------------ */
/* Death Watch + breath helpers — used by widgets + the hook            */
/* ------------------------------------------------------------------ */

/**
 * Compute the Death Watch trigger for a given snapshot.
 *
 * Returns false when no snapshot is loaded so the dashboard's first
 * paint doesn't flash the takeover surface before data hydrates.
 */
export function deathWatchActive(
  snapshot: AgentStateSnapshotData | null,
  initialBreath: number = readInitialBreath(),
): boolean {
  if (snapshot == null) return false;
  if (initialBreath <= 0) return false;
  return snapshot.breath / initialBreath < DEATH_WATCH_RATIO;
}

/** Compute the BREATH percentage (0..100); null when no snapshot. */
export function breathPct(
  snapshot: AgentStateSnapshotData | null,
  initialBreath: number = readInitialBreath(),
): number | null {
  if (snapshot == null) return null;
  if (initialBreath <= 0) return 0;
  const pct = (snapshot.breath / initialBreath) * 100;
  if (!Number.isFinite(pct)) return 0;
  return Math.max(0, Math.min(100, pct));
}

/* ------------------------------------------------------------------ */
/* wsStore lift — translate sandbox records into WS frames              */
/* ------------------------------------------------------------------ */

/**
 * Translate a sandbox decision record stream into the dashboard's
 * canonical DecisionFeedEntry shape. The settled-bet log is joined
 * in by `market_id` lookup; resulting WIN/LOSS rows render through
 * the existing DecisionFeed component without any UI changes.
 *
 * Pure: no React, no fetch, no side effects.
 */
export function toDecisionFeedEntries(
  decisions: readonly DecisionRecordData[],
  settled: readonly SettledBetRecordData[],
): DecisionFeedEntry[] {
  const settledByMarket = new Map<string, SettledBetRecordData>();
  for (const s of settled) {
    settledByMarket.set(s.market_id, s);
  }
  return decisions.map((d): DecisionFeedEntry => {
    const settledRow = d.market_id ? settledByMarket.get(d.market_id) : undefined;
    let result: "WIN" | "LOSS" | "PENDING" | undefined;
    if (d.kind === "BET" && settledRow) {
      result = settledRow.pnl_usd > 0 ? "WIN" : "LOSS";
    } else if (d.kind === "BET") {
      result = "PENDING";
    }
    const entry: DecisionFeedEntry = {
      id: `tick-${d.tick}`,
      ts: d.ts,
      action: d.kind,
      // Living Stage P1 (Codex diff-review M1): carry market_id so the poll
      // path's decisionFeed entries identify the market — without this Z3
      // "The Act" always falls to the idle "scanning" card even mid-bet.
      ...(d.market_id != null ? { market_id: d.market_id } : {}),
      ...(d.side != null ? { side: d.side } : {}),
      ...(d.kind === "BET" ? { size_usd: d.size_usd } : {}),
      ...(d.edge_pct != null ? { edge_pct: d.edge_pct } : {}),
      ...(result != null ? { result } : {}),
      ...(settledRow ? { pnl_usd: settledRow.pnl_usd } : {}),
      ...(d.no_bet_reason ? { reasoning: d.no_bet_reason } : {}),
      ...(d.odds_yes != null ? { odds_yes: d.odds_yes } : {}),
      ...(d.odds_no != null ? { odds_no: d.odds_no } : {}),
      ...(d.fee_floor_pct != null ? { fee_floor_pct: d.fee_floor_pct } : {}),
      ...(d.signal_scores && Object.keys(d.signal_scores).length
        ? { signals: d.signal_scores as EngineSignalMap }
        : {}),
    };
    return entry;
  });
}
