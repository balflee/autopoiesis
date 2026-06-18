import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, beforeEach } from "vitest";
import React from "react";

import "@/__tests__/setup";

import { FusionSignalsRail } from "@/components/living/FusionSignalsRail";
import { useWsStore } from "@/lib/wsStore";

/**
 * FusionSignalsRail (Z4) acceptance suite.
 *
 *   1. Loading fallback when no decision_feed frame has landed.
 *   2. Renders the 5 signed engine signal rows from the newest entry.
 *   3. Surfaces fused edge + fee floor from the newest entry.
 */

describe("FusionSignalsRail", () => {
  beforeEach(() => {
    useWsStore.getState().reset();
  });

  it("renders the rail header even before any decision_feed frame lands", () => {
    render(<FusionSignalsRail />);
    expect(screen.getByTestId("fusion-rail")).toBeInTheDocument();
    // No edge yet → em-dash placeholder.
    expect(screen.getByTestId("fused-edge")).toHaveTextContent("—");
  });

  it("renders 5 signed engine bars + fused edge vs fee floor from the newest entry", () => {
    render(<FusionSignalsRail />);
    act(() => {
      useWsStore.getState().ingest({
        kind: "decision_feed",
        ts: "t",
        seq: 1,
        entries: [
          {
            id: "d1",
            ts: "t",
            action: "BET",
            side: "YES",
            size_usd: 50,
            market_id: "m",
            edge_pct: 0.041,
            fee_floor_pct: 0.018,
            signals: {
              tennis_technical: 0.12,
              market_momentum: 0.08,
              surface_advantage: -0.05,
              head_to_head: 0.03,
              rest_recency: 0.01,
            },
          },
        ],
      } as any);
    });

    const root = screen.getByTestId("fusion-rail");
    // All 5 engine names present.
    expect(root).toHaveTextContent(/tennis_technical/i);
    expect(root).toHaveTextContent(/market_momentum/i);
    expect(root).toHaveTextContent(/surface_advantage/i);
    expect(root).toHaveTextContent(/head_to_head/i);
    expect(root).toHaveTextContent(/rest_recency/i);

    // Fused edge + fee floor surfaced from the newest entry.
    expect(screen.getByTestId("fused-edge")).toHaveTextContent("0.041");
    expect(screen.getByTestId("fee-floor")).toHaveTextContent("0.018");
  });
});
