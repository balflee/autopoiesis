import { describe, it, expect } from "vitest";

import { toDecisionFeedEntries } from "@/lib/sandbox_state_shared";
import type { DecisionRecordData } from "@/lib/sandbox_state_shared";

function dec(overrides: Partial<DecisionRecordData> = {}): DecisionRecordData {
  return {
    tick: 1,
    ts: "t",
    market_id: "Sinner def. Alcaraz?",
    kind: "BET",
    size_usd: 50,
    side: "YES",
    edge_pct: 0.041,
    no_bet_reason: null,
    breath_after: 72,
    bankroll_usd_after: 1240,
    odds_yes: 0.58,
    odds_no: 0.42,
    fee_floor_pct: 0.018,
    signal_scores: { tennis_technical: 0.12 },
    ...overrides,
  };
}

describe("toDecisionFeedEntries living-stage mapping", () => {
  it("carries market_id, odds, and signals onto the feed entry (poll path)", () => {
    const entry = toDecisionFeedEntries([dec()], [])[0]!;
    // Codex diff-review M1 fix: without market_id, Z3 'The Act' is stuck on the
    // idle 'scanning' card on the poll path even when the agent is betting.
    expect(entry.market_id).toBe("Sinner def. Alcaraz?");
    expect(entry.action).toBe("BET");
    expect(entry.odds_yes).toBe(0.58);
    expect(entry.odds_no).toBe(0.42);
    expect(entry.fee_floor_pct).toBe(0.018);
    expect(entry.signals).toEqual({ tennis_technical: 0.12 });
  });

  it("omits the living fields when absent (no spurious keys)", () => {
    const entry = toDecisionFeedEntries(
      [dec({ odds_yes: undefined, odds_no: undefined, fee_floor_pct: undefined, signal_scores: undefined, market_id: null })],
      [],
    )[0]!;
    expect(entry.market_id).toBeUndefined();
    expect(entry.odds_yes).toBeUndefined();
    expect(entry.signals).toBeUndefined();
  });
});
