import { describe, expect, it, beforeEach } from "vitest";

import "../setup";

import { useWsStore } from "../../lib/wsStore";
import type {
  DecisionFeedMessage,
  LlmActivatedMessage,
  PhaseTransitionMessage,
  WeightsUpdatedMessage,
} from "../../lib/types";

/**
 * wsStore v0.2.0 acceptance — the new state surfaces the sprint_4
 * components depend on (decisionFeed merge-by-id, weightsHistory ring
 * buffer, cumulativePnlHistory derivation, llmActivated one-shot
 * latch, phaseTransition state).
 */

describe("wsStore — v0.2.0 ingest", () => {
  beforeEach(() => {
    useWsStore.getState().reset();
  });

  it("merges decision_feed entries by id and keeps newest-first", () => {
    const first: DecisionFeedMessage = {
      kind: "decision_feed",
      ts: "2026-05-21T12:00:00Z",
      seq: 1,
      entries: [
        { id: "d1", ts: "2026-05-21T12:00:00Z", action: "BET", result: "PENDING" },
        { id: "d2", ts: "2026-05-21T12:01:00Z", action: "BET", result: "PENDING" },
      ],
    };
    const second: DecisionFeedMessage = {
      kind: "decision_feed",
      ts: "2026-05-21T12:05:00Z",
      seq: 2,
      entries: [
        { id: "d1", ts: "2026-05-21T12:04:00Z", action: "BET", result: "WIN", pnl_usd: 30 },
      ],
    };
    useWsStore.getState().ingest(first);
    useWsStore.getState().ingest(second);

    const feed = useWsStore.getState().decisionFeed;
    expect(feed).toHaveLength(2);
    // Newest first by ts.
    expect(feed[0]!.id).toBe("d1");
    expect(feed[0]!.result).toBe("WIN");
    expect(feed[1]!.id).toBe("d2");
  });

  it("derives cumulativePnlHistory from settled rows only", () => {
    const frame: DecisionFeedMessage = {
      kind: "decision_feed",
      ts: "2026-05-21T12:10:00Z",
      seq: 1,
      entries: [
        { id: "a", ts: "2026-05-21T12:00:00Z", action: "BET", result: "WIN", pnl_usd: 50 },
        { id: "b", ts: "2026-05-21T12:01:00Z", action: "BET", result: "PENDING" },
        { id: "c", ts: "2026-05-21T12:02:00Z", action: "BET", result: "LOSS", pnl_usd: -50 },
        { id: "d", ts: "2026-05-21T12:03:00Z", action: "BET", result: "WIN", pnl_usd: 50 },
      ],
    };
    useWsStore.getState().ingest(frame);
    const pnl = useWsStore.getState().cumulativePnlHistory;
    // 3 settled (a, c, d) — PENDING skipped.
    expect(pnl).toHaveLength(3);
    expect(pnl[pnl.length - 1]!.wins).toBe(2);
    expect(pnl[pnl.length - 1]!.losses).toBe(1);
    expect(pnl[pnl.length - 1]!.cumulative_pnl).toBe(50);
    expect(pnl[pnl.length - 1]!.win_rate).toBeCloseTo(2 / 3, 5);
  });

  it("pushes weights frames into weightsHistory in order", () => {
    const mk = (seq: number, w_r: number, beta: number): WeightsUpdatedMessage => ({
      kind: "weights_updated",
      ts: `2026-05-21T12:00:0${seq}Z`,
      seq,
      weights: { w_r, w_s: 1 - w_r, alpha: 0.5, beta, rho: 0 },
    });
    useWsStore.getState().ingest(mk(1, 0.9, 0));
    useWsStore.getState().ingest(mk(2, 0.7, 0.2));
    const hist = useWsStore.getState().weightsHistory;
    expect(hist).toHaveLength(2);
    expect(hist[0]!.beta).toBe(0);
    expect(hist[1]!.beta).toBeCloseTo(0.2);
  });

  it("captures phase_transition state with the full payload", () => {
    const frame: PhaseTransitionMessage = {
      kind: "phase_transition",
      ts: "2026-05-21T12:00:00Z",
      seq: 1,
      payload: { from: "PHASE_1_INFANCY", to: "PHASE_2_APPRENTICE", reason: "β₁ unfrozen" },
    };
    useWsStore.getState().ingest(frame);
    const pt = useWsStore.getState().phaseTransition;
    expect(pt).toBeTruthy();
    expect(pt!.payload.to).toBe("PHASE_2_APPRENTICE");
    expect(pt!.payload.reason).toBe("β₁ unfrozen");

    useWsStore.getState().dismissPhaseTransition();
    expect(useWsStore.getState().phaseTransition).toBeNull();
  });

  it("llm_activated stays idempotent — latch + note", () => {
    const a: LlmActivatedMessage = {
      kind: "llm_activated",
      ts: "x",
      seq: 1,
      note: "first",
    };
    const b: LlmActivatedMessage = { kind: "llm_activated", ts: "x", seq: 2 };
    useWsStore.getState().ingest(a);
    expect(useWsStore.getState().llmActivated).toBe(true);
    expect(useWsStore.getState().llmActivationNote).toBe("first");
    useWsStore.getState().ingest(b);
    expect(useWsStore.getState().llmActivated).toBe(true);
    // Second frame had no note — previous note preserved.
    expect(useWsStore.getState().llmActivationNote).toBe("first");

    // The "shown" latch is a separate flag flipped by the component.
    expect(useWsStore.getState().llmActivatedShown).toBe(false);
    useWsStore.getState().markLlmOverlayShown();
    expect(useWsStore.getState().llmActivatedShown).toBe(true);
  });
});
