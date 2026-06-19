import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, beforeEach } from "vitest";
import React from "react";

import "../../setup";

import { OpenPositions } from "@/components/living/OpenPositions";
import { useWsStore } from "@/lib/wsStore";

/**
 * Living Stage — OpenPositions panel acceptance suite.
 *
 *   1. Empty state (no bets) → "0 open" + a scanning note.
 *   2. Open bets → count + at-risk exposure + the recent bets list.
 *   3. Settled bets → W/L tally + net PnL, and they no longer count as open.
 *
 * Seeds the wsStore via the decision_feed ingest seam (no network mocking).
 */
describe("OpenPositions — betting surface", () => {
  beforeEach(() => {
    useWsStore.getState().reset();
  });

  it("shows the empty state when there are no bets", () => {
    render(<OpenPositions />);
    expect(screen.getByTestId("positions-summary")).toHaveTextContent(/0 open/);
    expect(screen.getByTestId("open-positions")).toHaveTextContent(/no bets yet/i);
  });

  it("shows open count, at-risk exposure, and the recent bets", () => {
    render(<OpenPositions />);
    act(() => {
      useWsStore.getState().ingest({
        kind: "decision_feed",
        ts: "t",
        seq: 1,
        entries: [
          {
            id: "b1",
            ts: "2026-06-19T07:00:00Z",
            action: "BET",
            side: "YES",
            size_usd: 2.74,
            market_id: "2585700",
            edge_pct: 0.227,
            result: "PENDING",
          },
          {
            id: "b2",
            ts: "2026-06-19T07:01:00Z",
            action: "BET",
            side: "NO",
            size_usd: 2.14,
            market_id: "2587850",
            edge_pct: 0.18,
            result: "PENDING",
          },
        ],
      } as any);
    });

    const summary = screen.getByTestId("positions-summary");
    expect(summary).toHaveTextContent("2 open");
    expect(summary).toHaveTextContent("$4.88 at risk");
    const list = screen.getByTestId("positions-list");
    expect(list).toHaveTextContent("2585700");
    expect(list).toHaveTextContent("2587850");
    expect(list).toHaveTextContent(/holding/);
  });

  it("tallies settled W/L and net PnL, excluding them from the open count", () => {
    render(<OpenPositions />);
    act(() => {
      useWsStore.getState().ingest({
        kind: "decision_feed",
        ts: "t",
        seq: 1,
        entries: [
          {
            id: "b1",
            ts: "2026-06-19T07:00:00Z",
            action: "BET",
            side: "YES",
            size_usd: 2.74,
            market_id: "m1",
            result: "WIN",
            pnl_usd: 1.8,
          },
          {
            id: "b2",
            ts: "2026-06-19T07:01:00Z",
            action: "BET",
            side: "NO",
            size_usd: 2.14,
            market_id: "m2",
            result: "LOSS",
            pnl_usd: -2.14,
          },
        ],
      } as any);
    });

    const settled = screen.getByTestId("positions-settled");
    expect(settled).toHaveTextContent(/1W.1L/); // 1W–1L
    expect(settled).toHaveTextContent(/0\.34/); // net = 1.8 - 2.14 = -0.34
    expect(screen.getByTestId("positions-summary")).toHaveTextContent("0 open");
  });
});
