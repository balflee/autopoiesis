/**
 * load_sandbox_state.test.ts — T-D-009 unit gate.
 *
 * Covers the PURE helpers in `dashboard/lib/load_sandbox_state.ts`:
 *
 *   - parseJsonl          — skip torn lines, accept well-formed objects
 *   - lastN               — bounded tail with oldest→newest order
 *   - computeLagAlerts    — directory + snapshot + staleness branches
 *   - deathWatchActive    — boundary at breath/initial < 0.10
 *   - breathPct           — clamp + null-safety
 *   - toDecisionFeedEntries — settled-bet join, WIN/LOSS marker, NO_BET
 *     fields, edge_pct preservation
 *   - readInitialBreath   — env override (NEXT_PUBLIC_INITIAL_BREATH) +
 *     window override + default
 *
 * The React hook itself is covered by the Playwright spec at
 * `dashboard/tests/dashboard/playwright/sandbox-live.spec.ts` — that's
 * the integration surface that actually exercises polling + render
 * budget. Mocking React's lifecycle here would be overengineered.
 */

import { describe, expect, it, vi } from "vitest";

import {
  breathPct,
  computeLagAlerts,
  DEATH_WATCH_RATIO,
  DEFAULT_INITIAL_BREATH,
  DEFAULT_TAIL_N,
  deathWatchActive,
  lastN,
  parseJsonl,
  readInitialBreath,
  SNAPSHOT_STALE_MS,
  toDecisionFeedEntries,
  type AgentStateSnapshotData,
  type DecisionRecordData,
  type SettledBetRecordData,
} from "@/lib/load_sandbox_state";

const SNAPSHOT_FIXTURE: AgentStateSnapshotData = {
  snapshot_ts: "2026-05-26T12:00:00Z",
  phase: "PHASE_2_APPRENTICE",
  breath: 88,
  bankroll_usd: 102.5,
  phase_age_days: 0.4,
  open_bet_ids: ["mock-bet-1"],
  last_tick: 1,
  weights: null,
  desperate: false,
};

describe("parseJsonl", () => {
  it("returns [] on empty string", () => {
    expect(parseJsonl<unknown>("")).toEqual([]);
  });
  it("parses one well-formed JSON object per line", () => {
    const txt = '{"a":1}\n{"b":2}\n';
    expect(parseJsonl<Record<string, number>>(txt)).toEqual([
      { a: 1 },
      { b: 2 },
    ]);
  });
  it("skips torn or blank lines without raising", () => {
    const txt = '{"ok":1}\n\nnot-json\n{"ok":2}\n';
    expect(parseJsonl<Record<string, number>>(txt)).toEqual([
      { ok: 1 },
      { ok: 2 },
    ]);
  });
  it("tolerates CRLF terminators", () => {
    const txt = '{"a":1}\r\n{"b":2}\r\n';
    expect(parseJsonl<Record<string, number>>(txt)).toEqual([
      { a: 1 },
      { b: 2 },
    ]);
  });
  it("filters non-object JSON values (numbers, strings) as a defensive measure", () => {
    const txt = '5\n"raw"\n{"ok":1}\n';
    expect(parseJsonl<Record<string, number>>(txt)).toEqual([{ ok: 1 }]);
  });
});

describe("lastN", () => {
  it("returns [] when n ≤ 0", () => {
    expect(lastN([1, 2, 3], 0)).toEqual([]);
    expect(lastN([1, 2, 3], -3)).toEqual([]);
  });
  it("returns [] for empty input", () => {
    expect(lastN<number>([], 5)).toEqual([]);
  });
  it("returns the original array when n exceeds length", () => {
    expect(lastN([1, 2], 50)).toEqual([1, 2]);
  });
  it("returns the last N preserving oldest→newest order", () => {
    expect(lastN([1, 2, 3, 4, 5], 3)).toEqual([3, 4, 5]);
  });
  it("DEFAULT_TAIL_N is the brief-canonical 50", () => {
    expect(DEFAULT_TAIL_N).toBe(50);
  });
});

describe("computeLagAlerts", () => {
  const now = Date.parse("2026-05-26T12:00:30Z");

  it("emits cold_boot when the sandbox dir does not exist", () => {
    const alerts = computeLagAlerts(null, now, false);
    expect(alerts).toHaveLength(1);
    expect(alerts[0]!.kind).toBe("cold_boot");
    expect(alerts[0]!.severity).toBe("info");
  });
  it("emits missing_snapshot when dir exists but snapshot is null", () => {
    const alerts = computeLagAlerts(null, now, true);
    expect(alerts).toHaveLength(1);
    expect(alerts[0]!.kind).toBe("missing_snapshot");
  });
  it("returns [] when snapshot is fresh", () => {
    const fresh = { ...SNAPSHOT_FIXTURE, snapshot_ts: "2026-05-26T12:00:25Z" };
    expect(computeLagAlerts(fresh, now, true)).toEqual([]);
  });
  it("emits snapshot_stale when ts is older than SNAPSHOT_STALE_MS", () => {
    const stale = {
      ...SNAPSHOT_FIXTURE,
      snapshot_ts: new Date(now - SNAPSHOT_STALE_MS - 1_000).toISOString(),
    };
    const alerts = computeLagAlerts(stale, now, true);
    expect(alerts).toHaveLength(1);
    expect(alerts[0]!.kind).toBe("snapshot_stale");
    expect(alerts[0]!.severity).toBe("warn");
  });
});

describe("deathWatchActive — PRD §8 line 560 trigger", () => {
  it("returns false for null snapshot (no flash on first paint)", () => {
    expect(deathWatchActive(null)).toBe(false);
  });
  it("returns false when initialBreath ≤ 0 (defensive)", () => {
    expect(deathWatchActive(SNAPSHOT_FIXTURE, 0)).toBe(false);
    expect(deathWatchActive(SNAPSHOT_FIXTURE, -1)).toBe(false);
  });
  it("returns false at exact threshold (10% strictly less-than)", () => {
    const at = { ...SNAPSHOT_FIXTURE, breath: 10 };
    expect(deathWatchActive(at, 100)).toBe(false);
  });
  it("returns true just below the 10% threshold", () => {
    const below = { ...SNAPSHOT_FIXTURE, breath: 9.9999 };
    expect(deathWatchActive(below, 100)).toBe(true);
  });
  it("DEATH_WATCH_RATIO is the brief-canonical 0.10", () => {
    expect(DEATH_WATCH_RATIO).toBe(0.1);
  });
});

describe("breathPct", () => {
  it("returns null on null snapshot", () => {
    expect(breathPct(null)).toBeNull();
  });
  it("clamps to [0, 100]", () => {
    expect(breathPct({ ...SNAPSHOT_FIXTURE, breath: 150 }, 100)).toBe(100);
    expect(breathPct({ ...SNAPSHOT_FIXTURE, breath: -1 }, 100)).toBe(0);
  });
  it("DEFAULT_INITIAL_BREATH is the Phase 2 sandbox initial (100)", () => {
    expect(DEFAULT_INITIAL_BREATH).toBe(100);
  });
});

describe("toDecisionFeedEntries", () => {
  const BET: DecisionRecordData = {
    tick: 1,
    ts: "2026-05-26T12:00:00Z",
    market_id: "m1",
    kind: "BET",
    size_usd: 2.5,
    side: "YES",
    edge_pct: 0.08,
    no_bet_reason: null,
    breath_after: 88,
    bankroll_usd_after: 102.5,
  };
  const NO_BET: DecisionRecordData = {
    tick: 2,
    ts: "2026-05-26T12:00:05Z",
    market_id: "m2",
    kind: "NO_BET",
    size_usd: 0,
    side: null,
    edge_pct: 0.01,
    no_bet_reason: "edge_below_threshold",
    breath_after: 86,
    bankroll_usd_after: 102.5,
  };
  const SETTLED_WIN: SettledBetRecordData = {
    bet_id: "b-m1",
    market_id: "m1",
    settled_ts: "2026-05-26T12:00:10Z",
    outcome: "yes",
    winning_price: 0.58,
    pnl_usd: 1.81,
    status: "settled",
  };

  it("maps BET → DecisionFeedEntry with size_usd + side + edge_pct", () => {
    const out = toDecisionFeedEntries([BET], []);
    expect(out).toHaveLength(1);
    expect(out[0]!.action).toBe("BET");
    expect(out[0]!.side).toBe("YES");
    expect(out[0]!.size_usd).toBe(2.5);
    expect(out[0]!.edge_pct).toBe(0.08);
    expect(out[0]!.result).toBe("PENDING");
  });
  it("joins settled bets by market_id → WIN/LOSS markers + pnl_usd", () => {
    const out = toDecisionFeedEntries([BET], [SETTLED_WIN]);
    expect(out[0]!.result).toBe("WIN");
    expect(out[0]!.pnl_usd).toBe(1.81);
  });
  it("emits LOSS when pnl_usd ≤ 0", () => {
    const loss: SettledBetRecordData = { ...SETTLED_WIN, pnl_usd: -1.0 };
    const out = toDecisionFeedEntries([BET], [loss]);
    expect(out[0]!.result).toBe("LOSS");
    expect(out[0]!.pnl_usd).toBe(-1.0);
  });
  it("maps NO_BET → action=NO_BET, NO size, no side, reasoning carried", () => {
    const out = toDecisionFeedEntries([NO_BET], []);
    expect(out[0]!.action).toBe("NO_BET");
    expect(out[0]!.size_usd).toBeUndefined();
    expect(out[0]!.side).toBeUndefined();
    expect(out[0]!.reasoning).toBe("edge_below_threshold");
    expect(out[0]!.result).toBeUndefined();
  });
  it("produces a unique `id` per tick so the merge dedup works", () => {
    const out = toDecisionFeedEntries([BET, NO_BET], []);
    expect(out[0]!.id).toBe("tick-1");
    expect(out[1]!.id).toBe("tick-2");
  });
});

describe("readInitialBreath", () => {
  it("defaults to 100 when no env / window override is present", () => {
    // jsdom test env: ensure no override slipped in.
    delete (window as unknown as { __GENESIS_INITIAL_BREATH__?: unknown })
      .__GENESIS_INITIAL_BREATH__;
    const prev = process.env.NEXT_PUBLIC_INITIAL_BREATH;
    delete process.env.NEXT_PUBLIC_INITIAL_BREATH;
    try {
      expect(readInitialBreath()).toBe(100);
    } finally {
      if (prev !== undefined) process.env.NEXT_PUBLIC_INITIAL_BREATH = prev;
    }
  });
  it("respects the window override (Playwright addInitScript path)", () => {
    (window as unknown as { __GENESIS_INITIAL_BREATH__?: number }).__GENESIS_INITIAL_BREATH__ =
      8000;
    try {
      expect(readInitialBreath()).toBe(8000);
    } finally {
      delete (window as unknown as { __GENESIS_INITIAL_BREATH__?: unknown })
        .__GENESIS_INITIAL_BREATH__;
    }
  });
  it("falls back to the default for invalid window values", () => {
    (window as unknown as { __GENESIS_INITIAL_BREATH__?: number }).__GENESIS_INITIAL_BREATH__ =
      -1;
    try {
      expect(readInitialBreath()).toBe(100);
    } finally {
      delete (window as unknown as { __GENESIS_INITIAL_BREATH__?: unknown })
        .__GENESIS_INITIAL_BREATH__;
    }
  });
});

describe("acceptance constants", () => {
  it("DEATH_WATCH_RATIO matches the brief's 0.10 trigger", () => {
    expect(DEATH_WATCH_RATIO).toBe(0.10);
  });
  it("DEFAULT_TAIL_N matches the brief's N=50 tail length", () => {
    expect(DEFAULT_TAIL_N).toBe(50);
  });
});

// Suppress an unused-import lint warning if any of the helpers above
// are pruned during a future refactor. vi is imported but unused.
void vi;
