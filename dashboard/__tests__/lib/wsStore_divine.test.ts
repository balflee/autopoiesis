import { describe, it, expect, beforeEach } from "vitest";

import {
  useWsStore,
  selectDivineEvents,
  selectDivineTreasury,
  selectIncarnationNumber,
  selectReincarnationLineage,
} from "@/lib/wsStore";

describe("wsStore divine slices", () => {
  beforeEach(() => useWsStore.getState().reset());

  it("setDivineState replaces the divine slices", () => {
    useWsStore.getState().setDivineState({
      events: [
        { type: "tithe", tithe_id: "h1", ts: "t", tick: 20, paid_usd: 20, breath_cost: 0, breath_after: 80, bankroll_after: 980 },
      ],
      treasury_usd: 2020,
      incarnation_number: 0,
      lineage: [
        { incarnation_number: 0, last_tick: 50, cause: "breath_zero", final_bankroll_usd: 0, ts: "t" },
      ],
    });
    const s = useWsStore.getState();
    expect(selectDivineEvents(s)).toHaveLength(1);
    expect(selectDivineTreasury(s)).toBe(2020);
    expect(selectIncarnationNumber(s)).toBe(0);
    expect(selectReincarnationLineage(s)).toHaveLength(1);
  });

  it("reset clears the divine slices", () => {
    useWsStore.getState().setDivineState({
      events: [], treasury_usd: 99, incarnation_number: 3, lineage: [],
    });
    useWsStore.getState().reset();
    expect(selectDivineTreasury(useWsStore.getState())).toBe(0);
    expect(selectIncarnationNumber(useWsStore.getState())).toBe(0);
  });
});
