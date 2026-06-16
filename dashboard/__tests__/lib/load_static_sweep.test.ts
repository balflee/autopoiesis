/**
 * load_static_sweep.test.ts — T-D-001 unit gate.
 *
 * Covers the validated static-sweep loader in `dashboard/lib/load_static_sweep.ts`:
 *
 *   - STATIC_SWEEP        — the bundled, committed fixture parses + validates,
 *     and its shape matches the data contract (optimal seed, 10-row frontier,
 *     sample bets with 5 slot signals, coverage).
 *   - validateFixture     — rejection paths: schema-version drift, missing
 *     sections, bad fraction/tuple/enum fields. (Happy path is exercised by the
 *     module-eval of STATIC_SWEEP itself.)
 *   - helpers             — isWin / sampleWinCount / sampleNetPnl.
 *
 * Mirrors the validated-fixture pattern of the training-journey loader; pure
 * functions, no DOM.
 */

import { describe, expect, it } from "vitest";

import {
  isWin,
  SIGNAL_SLOT_KEYS,
  SIGNAL_SLOT_LABEL,
  sampleNetPnl,
  sampleWinCount,
  STATIC_SWEEP,
  STATIC_SWEEP_SCHEMA_VERSION,
  validateFixture,
  type SampleBet,
  type StaticSweepFixture,
} from "@/lib/load_static_sweep";

/** A deep clone of the real fixture we can mutate per-test. */
function cloneFixture(): Record<string, unknown> {
  return JSON.parse(JSON.stringify(STATIC_SWEEP)) as Record<string, unknown>;
}

describe("STATIC_SWEEP (bundled fixture)", () => {
  it("parses + validates the committed JSON at module-eval", () => {
    expect(STATIC_SWEEP.schema_version).toBe(STATIC_SWEEP_SCHEMA_VERSION);
    expect(STATIC_SWEEP.task_id).toBe("T-D-001");
  });

  it("carries the optimal seed contract (weights + sizing + metrics)", () => {
    const seed = STATIC_SWEEP.optimal_seed;
    // Transcribed from reports/backtest/real_signal_sweep.md.
    expect(seed.weights.w_r).toBeCloseTo(0.564, 3);
    expect(seed.weights.w_s).toBeCloseTo(0.436, 3);
    expect(seed.weights.alpha).toEqual([0.486, 0.328, 0.186]);
    expect(seed.weights.beta).toEqual([0.443, 0.557]);
    expect(seed.weights.rho).toBeCloseTo(0.186, 3);
    expect(seed.sizing.max_breath_risk_pct).toBeCloseTo(0.232, 3);
    expect(seed.sizing.min_confidence).toBeCloseTo(0.049, 3);
    expect(seed.sizing.min_bet_size_usd).toBe(4.0);
    expect(seed.sharpe).toBeCloseTo(0.649, 3);
    expect(seed.bets).toBe(65);
    expect(seed.win_rate).toBeCloseTo(0.815, 3);
    expect(seed.net_pnl).toBeCloseTo(852.56, 2);
  });

  it("alpha + beta simplex components sum to 1", () => {
    const a = STATIC_SWEEP.optimal_seed.weights.alpha;
    const b = STATIC_SWEEP.optimal_seed.weights.beta;
    expect(a[0] + a[1] + a[2]).toBeCloseTo(1.0, 3);
    expect(b[0] + b[1]).toBeCloseTo(1.0, 3);
  });

  it("has a 10-row frontier ranked 1..10 by descending Sharpe", () => {
    expect(STATIC_SWEEP.frontier).toHaveLength(10);
    STATIC_SWEEP.frontier.forEach((row, i) => {
      expect(row.rank).toBe(i + 1);
      expect(row.win_rate).toBeGreaterThan(0);
      expect(row.win_rate).toBeLessThanOrEqual(1);
    });
    const sharpes = STATIC_SWEEP.frontier.map((r) => r.sharpe);
    const sorted = [...sharpes].sort((x, y) => y - x);
    expect(sharpes).toEqual(sorted);
    // Rank 1 is the optimal seed.
    expect(STATIC_SWEEP.frontier[0]?.sharpe).toBeCloseTo(
      STATIC_SWEEP.optimal_seed.sharpe,
      3,
    );
  });

  it("coverage is the reported 65.7%", () => {
    expect(STATIC_SWEEP.coverage_pct).toBeCloseTo(65.7, 1);
  });

  it("sample bets carry all 5 slot signals + a valid side/outcome", () => {
    expect(STATIC_SWEEP.sample_bets.length).toBeGreaterThanOrEqual(8);
    for (const bet of STATIC_SWEEP.sample_bets) {
      expect(bet.players).toHaveLength(2);
      expect(bet.entry_price).toBeGreaterThanOrEqual(0);
      expect(bet.entry_price).toBeLessThanOrEqual(1);
      expect(["YES", "NO"]).toContain(bet.side);
      expect(["yes", "no", "void"]).toContain(bet.outcome);
      for (const key of SIGNAL_SLOT_KEYS) {
        expect(typeof bet.signals[key]).toBe("number");
        expect(Number.isFinite(bet.signals[key])).toBe(true);
      }
    }
  });

  it("exposes a display label for every slot key", () => {
    for (const key of SIGNAL_SLOT_KEYS) {
      expect(SIGNAL_SLOT_LABEL[key]).toBeTruthy();
    }
  });
});

describe("validateFixture rejection paths", () => {
  it("rejects a schema_version mismatch", () => {
    const f = cloneFixture();
    f.schema_version = "9.9.9";
    expect(() => validateFixture(f)).toThrow(/schema_version mismatch/);
  });

  it("rejects a non-object root", () => {
    expect(() => validateFixture(null)).toThrow(/root must be an object/);
    expect(() => validateFixture([])).toThrow(/root must be an object/);
  });

  it("rejects a missing frontier array", () => {
    const f = cloneFixture();
    delete f.frontier;
    expect(() => validateFixture(f)).toThrow(/frontier must be an array/);
  });

  it("rejects a missing sample_bets array", () => {
    const f = cloneFixture();
    delete f.sample_bets;
    expect(() => validateFixture(f)).toThrow(/sample_bets must be an array/);
  });

  it("rejects an out-of-range win_rate fraction", () => {
    const f = cloneFixture();
    (f.optimal_seed as Record<string, unknown>).win_rate = 1.5;
    expect(() => validateFixture(f)).toThrow(/win_rate must be in \[0, 1\]/);
  });

  it("rejects a wrong-length alpha tuple", () => {
    const f = cloneFixture();
    ((f.optimal_seed as Record<string, unknown>).weights as Record<
      string,
      unknown
    >).alpha = [0.5, 0.5];
    expect(() => validateFixture(f)).toThrow(/alpha must be an array of length 3/);
  });

  it("rejects an invalid bet side", () => {
    const f = cloneFixture();
    const bets = f.sample_bets as Record<string, unknown>[];
    (bets[0] as Record<string, unknown>).side = "MAYBE";
    expect(() => validateFixture(f)).toThrow(/side must be "YES" or "NO"/);
  });

  it("rejects a sample bet missing a slot signal", () => {
    const f = cloneFixture();
    const bets = f.sample_bets as Record<string, unknown>[];
    delete (
      (bets[0] as Record<string, unknown>).signals as Record<string, unknown>
    ).rest_recency;
    expect(() => validateFixture(f)).toThrow(/signals\.rest_recency/);
  });

  it("rejects a non-2-element players tuple", () => {
    const f = cloneFixture();
    const bets = f.sample_bets as Record<string, unknown>[];
    (bets[0] as Record<string, unknown>).players = ["Solo"];
    expect(() => validateFixture(f)).toThrow(/players must be a 2-element array/);
  });

  it("accepts a well-formed hand-built fixture round-trip", () => {
    const f = cloneFixture();
    const validated: StaticSweepFixture = validateFixture(f);
    expect(validated.optimal_seed.bets).toBe(STATIC_SWEEP.optimal_seed.bets);
    expect(validated.frontier).toHaveLength(10);
  });
});

describe("helpers", () => {
  const winBet: SampleBet = {
    market_id: "m1",
    players: ["A", "B"],
    surface: "Hard",
    entry_price: 0.5,
    outcome: "yes",
    signals: {
      tennis_technical: 0.1,
      market_momentum: 0.1,
      surface_advantage: 0.1,
      head_to_head: 0.1,
      rest_recency: 0.1,
    },
    side: "YES",
    size: 4,
    pnl: 3.2,
  };
  const lossBet: SampleBet = { ...winBet, market_id: "m2", pnl: -4 };
  const voidBet: SampleBet = { ...winBet, market_id: "m3", outcome: "void", pnl: 0 };

  it("isWin is strictly-positive pnl (void/loss are not wins)", () => {
    expect(isWin(winBet)).toBe(true);
    expect(isWin(lossBet)).toBe(false);
    expect(isWin(voidBet)).toBe(false);
  });

  it("sampleWinCount + sampleNetPnl fold over a fixture", () => {
    const fixture = {
      ...STATIC_SWEEP,
      sample_bets: [winBet, lossBet, voidBet],
    } as StaticSweepFixture;
    expect(sampleWinCount(fixture)).toBe(1);
    expect(sampleNetPnl(fixture)).toBeCloseTo(-0.8, 6);
  });

  it("default-argument helpers operate on the bundled fixture", () => {
    expect(sampleWinCount()).toBe(
      STATIC_SWEEP.sample_bets.filter((b) => b.pnl > 0).length,
    );
    expect(sampleNetPnl()).toBeCloseTo(
      STATIC_SWEEP.sample_bets.reduce((a, b) => a + b.pnl, 0),
      6,
    );
  });
});
