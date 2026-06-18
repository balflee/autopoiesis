import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";

import { useSandboxState } from "@/lib/load_sandbox_state";
import { useWsStore, selectDivineTreasury } from "@/lib/wsStore";

function bundle() {
  return {
    snapshot: {
      snapshot_ts: "t",
      phase: "PHASE_2_APPRENTICE",
      breath: 72,
      bankroll_usd: 1240,
      phase_age_days: 0,
      open_bet_ids: [],
      last_tick: 50,
      weights: null,
      desperate: false,
      incarnation_number: 0,
    },
    recent_decisions: [],
    recent_settled: [],
    lag_alerts: [],
    served_ts: "t",
    is_mock: false,
    recent_gods_treasury: [
      { type: "tithe", tithe_id: "h1", ts: "t", tick: 20, paid_usd: 20, breath_cost: 0, breath_after: 80, bankroll_after: 980 },
    ],
    gods_revenue_cumulative_usd: 20,
    incarnation_number: 0,
    incarnation_lineage: [],
  };
}

describe("useSandboxState lifts divine fields", () => {
  beforeEach(() => useWsStore.getState().reset());

  it("pushes the divine slices from the bundle via setDivineState", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue({ ok: true, json: async () => bundle() }) as unknown as typeof fetch;
    renderHook(() => useSandboxState({ fetchImpl, pollMs: 10 }));
    await waitFor(() =>
      expect(selectDivineTreasury(useWsStore.getState())).toBe(20),
    );
  });
});
