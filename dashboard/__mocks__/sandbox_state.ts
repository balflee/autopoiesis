/**
 * dashboard/__mocks__/sandbox_state.ts — deterministic in-memory mock
 *
 * Used ONLY when `process.env.SANDBOX_TEST === "1"` is set. The
 * `/api/sandbox` route consults the test gate and, when active,
 * serves successive snapshots from this fixture instead of touching
 * `state/sandbox/` on disk. That keeps the Playwright spec hermetic
 * (no Track B runtime needed) while still exercising the same
 * loader + render path the production build uses.
 *
 * The fixture emits THREE mock decisions over ~10s (matches the brief's
 * `SANDBOX_TEST=1 produces 3 mock decisions over 10s` requirement).
 * Each call to {@link nextMockTick} bumps an internal counter modulo
 * the script length so the test can observe live changes.
 *
 * NO REAL DATA is committed under this module — it's a deterministic
 * fixture, NOT a production seed.
 */

import type {
  AgentStateSnapshotData,
  DecisionRecordData,
  LagAlert,
  SandboxStateBundle,
  SettledBetRecordData,
} from "@/lib/load_sandbox_state";

/** Anchor wall-clock for the fixture so tests can compare ts strings. */
const ANCHOR_TS = "2026-05-26T12:00:00Z";

function isoOffset(seconds: number): string {
  const base = Date.parse(ANCHOR_TS);
  return new Date(base + seconds * 1_000).toISOString().replace(/\.\d{3}Z$/, "Z");
}

/** The deterministic decision script (newest first by `tick` ascending). */
const DECISION_SCRIPT: readonly DecisionRecordData[] = [
  {
    tick: 1,
    ts: isoOffset(0),
    market_id: "polymarket-tennis-7771-yes",
    kind: "BET",
    size_usd: 2.5,
    side: "YES",
    edge_pct: 0.084,
    no_bet_reason: null,
    breath_after: 88,
    bankroll_usd_after: 102.5,
  },
  {
    tick: 2,
    ts: isoOffset(3),
    market_id: "polymarket-tennis-7782-no",
    kind: "NO_BET",
    size_usd: 0.0,
    side: null,
    edge_pct: 0.012,
    no_bet_reason: "edge_below_threshold",
    breath_after: 86,
    bankroll_usd_after: 102.5,
  },
  {
    tick: 3,
    ts: isoOffset(6),
    market_id: "polymarket-tennis-7790-yes",
    kind: "BET",
    size_usd: 4.25,
    side: "NO",
    edge_pct: 0.061,
    no_bet_reason: null,
    breath_after: 84,
    bankroll_usd_after: 98.25,
  },
] as const;

/** Settled-bet script — sized 1:1 with decisions so the feed has companions. */
const SETTLED_SCRIPT: readonly SettledBetRecordData[] = [
  {
    bet_id: "mock-bet-7771",
    market_id: "polymarket-tennis-7771-yes",
    settled_ts: isoOffset(2),
    outcome: "yes",
    winning_price: 0.58,
    pnl_usd: 1.81,
    status: "settled",
  },
];

/**
 * Pre-baked snapshot stages. Index 0 is the cold-start snapshot; each
 * subsequent stage advances `last_tick` so the dashboard can render a
 * sequence of frames during the spec.
 */
const SNAPSHOT_STAGES: readonly AgentStateSnapshotData[] = [
  {
    snapshot_ts: isoOffset(0),
    phase: "PHASE_2_APPRENTICE",
    breath: 88,
    bankroll_usd: 102.5,
    phase_age_days: 0.4,
    open_bet_ids: ["mock-bet-7771"],
    last_tick: 1,
    weights: null,
    desperate: false,
  },
  {
    snapshot_ts: isoOffset(3),
    phase: "PHASE_2_APPRENTICE",
    breath: 86,
    bankroll_usd: 102.5,
    phase_age_days: 0.4,
    open_bet_ids: ["mock-bet-7771"],
    last_tick: 2,
    weights: null,
    desperate: false,
  },
  {
    snapshot_ts: isoOffset(6),
    phase: "PHASE_2_APPRENTICE",
    breath: 84,
    bankroll_usd: 98.25,
    phase_age_days: 0.4,
    open_bet_ids: ["mock-bet-7771", "mock-bet-7790"],
    last_tick: 3,
    weights: null,
    desperate: false,
  },
] as const;

/** State of the mock generator — bumped each time the route serves. */
let cursor = 0;

/** Reset the cursor — Playwright `beforeEach` calls this via the route. */
export function resetMockSandbox(): void {
  cursor = 0;
}

/**
 * One advancement of the mock — returns the bundle the route should serve.
 * After the script is exhausted, the last stage is served indefinitely so
 * the dashboard does not "blink to empty" when the test idles.
 */
export function nextMockTick(): SandboxStateBundle {
  const idx = Math.min(cursor, SNAPSHOT_STAGES.length - 1);
  const stage = SNAPSHOT_STAGES[idx];
  // Pre-condition: SNAPSHOT_STAGES is non-empty, so idx is always a valid index.
  if (stage === undefined) {
    throw new Error("internal: SNAPSHOT_STAGES is empty");
  }
  const decisionsThruIdx = DECISION_SCRIPT.slice(0, idx + 1);
  const settledThruIdx = SETTLED_SCRIPT.filter(
    (s) => Date.parse(s.settled_ts) <= Date.parse(stage.snapshot_ts),
  );
  cursor = Math.min(cursor + 1, SNAPSHOT_STAGES.length); // freeze on last
  return {
    snapshot: stage,
    recent_decisions: decisionsThruIdx,
    recent_settled: settledThruIdx,
    lag_alerts: stage.last_tick === 0 ? [STALE_BOOT_ALERT] : [],
    /** Wall-clock when this bundle was assembled — drives staleness UI. */
    served_ts: new Date().toISOString(),
    /** True if the bundle came from this in-memory mock. */
    is_mock: true,
  };
}

const STALE_BOOT_ALERT: LagAlert = {
  kind: "cold_boot",
  detail: "sandbox runtime has not yet produced any decisions",
  severity: "info",
};

/** Pre-fab "loader sees no state on disk" bundle — used by route fallback. */
export const EMPTY_BUNDLE: SandboxStateBundle = {
  snapshot: null,
  recent_decisions: [],
  recent_settled: [],
  lag_alerts: [STALE_BOOT_ALERT],
  served_ts: new Date(0).toISOString(),
  is_mock: false,
};
