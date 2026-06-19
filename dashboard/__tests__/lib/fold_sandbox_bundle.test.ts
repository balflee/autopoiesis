/**
 * fold_sandbox_bundle.test.ts — the SINGLE bundle-assembly fold.
 *
 * `foldSandboxBundle` is the one place the SandboxStateBundle is built
 * from raw file strings; both the local-fs loader and the
 * over-the-backend loader call it. These tests pin the semantics that
 * used to live inline in `loadSandboxBundle`:
 *
 *   1. cold_boot when dirExists=false
 *   2. missing_snapshot when dir exists but snapshot is null
 *   3. full fixture → snapshot + tails + zero alerts
 *   4. treasury cumulative = successful tributes + cash tithes ONLY
 *      (breath-paid tithe NOT converted to USD — the honesty rule)
 *   5. extraAlerts are appended after the computed alerts
 *   6. torn/garbage snapshot string → snapshot null, no throw
 */

import { describe, expect, it } from "vitest";

import {
  EMPTY_RAW_SANDBOX_FILES,
  foldSandboxBundle,
  type RawSandboxFiles,
} from "@/lib/sandbox_state_shared";

const FROZEN_NOW = Date.parse("2026-06-19T12:01:00Z");

const SNAPSHOT_JSON = JSON.stringify({
  snapshot_ts: "2026-06-19T12:00:55Z",
  phase: "PHASE_2_APPRENTICE",
  breath: 88,
  bankroll_usd: 102.5,
  phase_age_days: 0.4,
  open_bet_ids: ["b1"],
  last_tick: 1,
  weights: null,
  desperate: false,
  incarnation_number: 3,
});

describe("foldSandboxBundle", () => {
  it("emits cold_boot for the all-empty raw input", () => {
    const bundle = foldSandboxBundle(EMPTY_RAW_SANDBOX_FILES, FROZEN_NOW);
    expect(bundle.snapshot).toBeNull();
    expect(bundle.recent_decisions).toEqual([]);
    expect(bundle.recent_gods_treasury).toEqual([]);
    expect(bundle.gods_revenue_cumulative_usd).toBe(0);
    expect(bundle.lag_alerts.map((a) => a.kind)).toContain("cold_boot");
    expect(bundle.is_mock).toBe(false);
  });

  it("emits missing_snapshot when dir exists but snapshot is null", () => {
    const raw: RawSandboxFiles = {
      ...EMPTY_RAW_SANDBOX_FILES,
      dirExists: true,
    };
    const bundle = foldSandboxBundle(raw, FROZEN_NOW);
    expect(bundle.lag_alerts.map((a) => a.kind)).toContain("missing_snapshot");
  });

  it("folds a full fixture with zero alerts and carries incarnation_number", () => {
    const raw: RawSandboxFiles = {
      dirExists: true,
      snapshot: SNAPSHOT_JSON,
      decisions: JSON.stringify({ tick: 1, market_id: "m1", kind: "BET" }) + "\n",
      settled:
        JSON.stringify({
          bet_id: "b1",
          market_id: "m1",
          settled_ts: "2026-06-19T12:00:58Z",
          outcome: "yes",
          winning_price: 0.58,
          pnl_usd: 1.81,
          status: "settled",
        }) + "\n",
      treasury: null,
      deaths: null,
    };
    const bundle = foldSandboxBundle(raw, FROZEN_NOW);
    expect(bundle.snapshot?.breath).toBe(88);
    expect(bundle.incarnation_number).toBe(3);
    expect(bundle.recent_decisions).toHaveLength(1);
    expect(bundle.recent_settled).toHaveLength(1);
    expect(bundle.lag_alerts).toEqual([]);
  });

  it("counts successful tributes + cash tithes but NOT breath-paid tithes", () => {
    const treasury =
      [
        JSON.stringify({ type: "tribute", success: true, amount_usd: 500 }),
        JSON.stringify({ type: "tribute", success: false, amount_usd: 2000 }), // failed → excluded
        JSON.stringify({ type: "tithe", paid_usd: 12.5 }), // cash tithe → counted
        JSON.stringify({ type: "tithe", paid_usd: 0 }), // breath-paid → adds 0
      ].join("\n") + "\n";
    const raw: RawSandboxFiles = {
      ...EMPTY_RAW_SANDBOX_FILES,
      dirExists: true,
      snapshot: SNAPSHOT_JSON,
      treasury,
    };
    const bundle = foldSandboxBundle(raw, FROZEN_NOW);
    // 500 (successful tribute) + 12.5 (cash tithe). The failed tribute's
    // 2000 and the breath-paid tithe are excluded.
    expect(bundle.gods_revenue_cumulative_usd).toBe(512.5);
    expect(bundle.recent_gods_treasury).toHaveLength(4);
  });

  it("appends extraAlerts after the computed alerts", () => {
    const bundle = foldSandboxBundle(EMPTY_RAW_SANDBOX_FILES, FROZEN_NOW, 50, [
      { kind: "fs_error", detail: "boom", severity: "error" },
    ]);
    const kinds = bundle.lag_alerts.map((a) => a.kind);
    expect(kinds).toEqual(["cold_boot", "fs_error"]);
  });

  it("does not throw on a torn snapshot string", () => {
    const raw: RawSandboxFiles = {
      ...EMPTY_RAW_SANDBOX_FILES,
      dirExists: true,
      snapshot: '{"breath": 8', // truncated mid-write
    };
    const bundle = foldSandboxBundle(raw, FROZEN_NOW);
    expect(bundle.snapshot).toBeNull();
    // dir exists + snapshot unparseable → treated as missing.
    expect(bundle.lag_alerts.map((a) => a.kind)).toContain("missing_snapshot");
  });
});
