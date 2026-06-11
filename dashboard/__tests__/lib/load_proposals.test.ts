/**
 * Unit tests for lib/load_proposals — T-D-012 sprint_10.
 *
 * Covers the new fold + paging helpers that the Pending / History tabs
 * depend on. Pure functions, no DOM — these can run under the vanilla
 * vitest pool without React-testing-library.
 */
import { describe, expect, it } from "vitest";

import {
  effectiveStatus,
  foldByLatestStatus,
  HISTORY_PAGE_SIZE,
  isStrategyProposal,
  type StrategyProposal,
} from "../../lib/load_proposals";

const baseProposal: StrategyProposal = {
  proposal_id: "p1",
  ts: "2026-05-27T18:30:00.000Z",
  kind: "weight_delta",
  rationale: "rationale",
  proposed_change: { key: "alpha_2", delta: 0.06 },
  expected_impact: "+1% Sharpe",
  confidence_pct: 70,
  requires_human_approval: true,
};

describe("effectiveStatus", () => {
  it("returns 'pending' when status is undefined", () => {
    expect(effectiveStatus(baseProposal)).toBe("pending");
  });
  it("returns the explicit status when set", () => {
    expect(effectiveStatus({ ...baseProposal, status: "approved" })).toBe(
      "approved",
    );
    expect(effectiveStatus({ ...baseProposal, status: "rejected" })).toBe(
      "rejected",
    );
  });
});

describe("foldByLatestStatus", () => {
  it("returns empty pending + history for an empty list", () => {
    const result = foldByLatestStatus([]);
    expect(result.pending).toEqual([]);
    expect(result.history).toEqual([]);
  });

  it("classifies pending vs decided proposals", () => {
    const pending: StrategyProposal = {
      ...baseProposal,
      proposal_id: "p_pending",
      status: "pending",
    };
    const approved: StrategyProposal = {
      ...baseProposal,
      proposal_id: "p_approved",
      ts: "2026-05-27T19:00:00.000Z",
      status: "approved",
    };
    const rejected: StrategyProposal = {
      ...baseProposal,
      proposal_id: "p_rejected",
      ts: "2026-05-27T20:00:00.000Z",
      status: "rejected",
    };
    const result = foldByLatestStatus([pending, approved, rejected]);
    expect(result.pending.map((p) => p.proposal_id)).toEqual(["p_pending"]);
    // History is newest-first.
    expect(result.history.map((p) => p.proposal_id)).toEqual([
      "p_rejected",
      "p_approved",
    ]);
  });

  it("folds latest-status-wins across multiple rows of the same id", () => {
    const transitions: StrategyProposal[] = [
      {
        ...baseProposal,
        proposal_id: "p_x",
        ts: "2026-05-27T18:00:00.000Z",
        status: "pending",
      },
      {
        ...baseProposal,
        proposal_id: "p_x",
        ts: "2026-05-27T19:00:00.000Z",
        status: "approved",
      },
    ];
    const result = foldByLatestStatus(transitions);
    expect(result.pending).toEqual([]);
    expect(result.history).toHaveLength(1);
    expect(result.history[0]?.status).toBe("approved");
  });

  it("truncates history to HISTORY_PAGE_SIZE by default", () => {
    const rows: StrategyProposal[] = Array.from({ length: 25 }, (_, i) => ({
      ...baseProposal,
      proposal_id: `p_${i.toString().padStart(2, "0")}`,
      // Ascending ts so the fold sees them in time order.
      ts: new Date(2026, 4, 27, 18, i, 0).toISOString(),
      status: "approved",
    }));
    const result = foldByLatestStatus(rows);
    expect(result.history).toHaveLength(HISTORY_PAGE_SIZE);
    // Most recent (highest index) wins the top of the newest-first list.
    expect(result.history[0]?.proposal_id).toBe("p_24");
  });

  it("honours an explicit historyLimit override", () => {
    const rows: StrategyProposal[] = Array.from({ length: 30 }, (_, i) => ({
      ...baseProposal,
      proposal_id: `p_${i}`,
      ts: new Date(2026, 4, 27, 18, i, 0).toISOString(),
      status: "rejected",
    }));
    const result = foldByLatestStatus(rows, 5);
    expect(result.history).toHaveLength(5);
  });
});

describe("isStrategyProposal", () => {
  it("accepts a v0.2.0 proposal with status field", () => {
    expect(isStrategyProposal({ ...baseProposal, status: "approved" })).toBe(
      true,
    );
  });

  it("accepts a v0.1.0 proposal without status field", () => {
    expect(isStrategyProposal(baseProposal)).toBe(true);
  });

  it("rejects malformed input", () => {
    expect(isStrategyProposal(null)).toBe(false);
    expect(isStrategyProposal({ proposal_id: "x" })).toBe(false);
  });
});
