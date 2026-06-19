/**
 * load_sandbox_state_server.test.ts — T-D-009 server-loader gate.
 *
 * Drives `loadSandboxBundle` against a tmp directory layout to verify
 * the four perimeter cases the brief lists:
 *
 *   1. Directory does not exist → cold_boot alert, empty bundle
 *   2. Directory exists but snapshot/JSONL absent → missing_snapshot
 *   3. Full fixture → snapshot + last-50 tails + zero alerts
 *   4. Old snapshot ts → snapshot_stale alert
 *
 * Tests use `node:os.tmpdir()` for isolation; teardown removes the dir.
 */

import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  type AgentStateSnapshotData,
  type DecisionRecordData,
  type SettledBetRecordData,
  SNAPSHOT_STALE_MS,
} from "@/lib/load_sandbox_state";
import { loadSandboxBundle } from "@/lib/load_sandbox_state.server";

const FROZEN_NOW = Date.parse("2026-05-26T12:01:00Z");

const SNAPSHOT: AgentStateSnapshotData = {
  snapshot_ts: "2026-05-26T12:00:55Z",
  phase: "PHASE_2_APPRENTICE",
  breath: 88,
  bankroll_usd: 102.5,
  phase_age_days: 0.4,
  open_bet_ids: ["b1"],
  last_tick: 1,
  weights: null,
  desperate: false,
};

describe("loadSandboxBundle", () => {
  let root: string;

  beforeEach(async () => {
    root = await fs.mkdtemp(path.join(os.tmpdir(), "sandbox-state-"));
  });
  afterEach(async () => {
    try {
      await fs.rm(root, { recursive: true, force: true });
    } catch {
      /* best-effort */
    }
  });

  it("emits cold_boot when the directory does not exist", async () => {
    const missing = path.join(root, "does-not-exist");
    const bundle = await loadSandboxBundle({
      root: missing,
      now: () => FROZEN_NOW,
    });
    expect(bundle.snapshot).toBeNull();
    expect(bundle.recent_decisions).toEqual([]);
    expect(bundle.recent_settled).toEqual([]);
    expect(bundle.lag_alerts.map((a) => a.kind)).toContain("cold_boot");
    expect(bundle.is_mock).toBe(false);
  });

  it("emits missing_snapshot when directory exists but files do not", async () => {
    const bundle = await loadSandboxBundle({ root, now: () => FROZEN_NOW });
    expect(bundle.snapshot).toBeNull();
    expect(bundle.lag_alerts.map((a) => a.kind)).toContain("missing_snapshot");
  });

  it("reads a full fixture without lag alerts", async () => {
    const dec: DecisionRecordData = {
      tick: 1,
      ts: "2026-05-26T12:00:55Z",
      market_id: "m1",
      kind: "BET",
      size_usd: 2.5,
      side: "YES",
      edge_pct: 0.08,
      no_bet_reason: null,
      breath_after: 88,
      bankroll_usd_after: 102.5,
    };
    const set: SettledBetRecordData = {
      bet_id: "b1",
      market_id: "m1",
      settled_ts: "2026-05-26T12:00:58Z",
      outcome: "yes",
      winning_price: 0.58,
      pnl_usd: 1.81,
      status: "settled",
    };
    await fs.writeFile(
      path.join(root, "agent_state.json"),
      JSON.stringify(SNAPSHOT),
    );
    await fs.writeFile(
      path.join(root, "decisions.jsonl"),
      JSON.stringify(dec) + "\n",
    );
    await fs.writeFile(
      path.join(root, "settled_bets.jsonl"),
      JSON.stringify(set) + "\n",
    );

    const bundle = await loadSandboxBundle({ root, now: () => FROZEN_NOW });
    expect(bundle.snapshot?.breath).toBe(88);
    expect(bundle.recent_decisions).toHaveLength(1);
    expect(bundle.recent_decisions[0]!.tick).toBe(1);
    expect(bundle.recent_settled).toHaveLength(1);
    expect(bundle.lag_alerts).toEqual([]);
  });

  it("emits fs_error (not just missing_snapshot) for a torn snapshot file", async () => {
    // A present-but-unparseable agent_state.json (mid-write tear) must
    // surface an `fs_error` — the pre-fold behaviour. Regression guard for
    // the fold refactor, which could have downgraded it to a silent
    // `missing_snapshot` (info) by routing the parse through the fold.
    await fs.writeFile(
      path.join(root, "agent_state.json"),
      '{"breath": 8', // truncated JSON
    );
    const bundle = await loadSandboxBundle({ root, now: () => FROZEN_NOW });
    const kinds = bundle.lag_alerts.map((a) => a.kind);
    expect(kinds).toContain("fs_error");
    expect(kinds).toContain("missing_snapshot");
    expect(bundle.snapshot).toBeNull();
  });

  it("emits snapshot_stale when snapshot_ts is older than SNAPSHOT_STALE_MS", async () => {
    const stale = {
      ...SNAPSHOT,
      snapshot_ts: new Date(FROZEN_NOW - SNAPSHOT_STALE_MS - 1_000).toISOString(),
    };
    await fs.writeFile(
      path.join(root, "agent_state.json"),
      JSON.stringify(stale),
    );
    const bundle = await loadSandboxBundle({ root, now: () => FROZEN_NOW });
    expect(bundle.lag_alerts.map((a) => a.kind)).toContain("snapshot_stale");
  });

  it("honours the tailN option to bound JSONL output", async () => {
    await fs.writeFile(
      path.join(root, "agent_state.json"),
      JSON.stringify(SNAPSHOT),
    );
    const rows: string[] = [];
    for (let i = 0; i < 75; i++) {
      rows.push(
        JSON.stringify({
          tick: i,
          ts: "2026-05-26T12:00:55Z",
          market_id: "m" + i,
          kind: "BET",
          size_usd: 1,
          side: "YES",
          edge_pct: 0.05,
          no_bet_reason: null,
          breath_after: 88,
          bankroll_usd_after: 100,
        }),
      );
    }
    await fs.writeFile(
      path.join(root, "decisions.jsonl"),
      rows.join("\n") + "\n",
    );
    const bundle = await loadSandboxBundle({
      root,
      tailN: 50,
      now: () => FROZEN_NOW,
    });
    expect(bundle.recent_decisions).toHaveLength(50);
    // Oldest→newest order; the LAST element is the freshest tick.
    expect(bundle.recent_decisions[0]!.tick).toBe(25);
    expect(bundle.recent_decisions[49]!.tick).toBe(74);
  });
});
