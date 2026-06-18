import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, beforeEach } from "vitest";
import React from "react";

import "../../setup";

import { CurrentMarketCard } from "@/components/living/CurrentMarketCard";
import { useWsStore } from "@/lib/wsStore";

/**
 * Living Stage — Zone Z3 · CurrentMarketCard ("The Act") acceptance suite.
 *
 *   1. A live BET on a market renders the market id, YES/NO odds, and the bet.
 *   2. A NO_BET newest entry falls back to the idle "scanning" card.
 *
 * Seeds the wsStore via the decision_feed ingest seam (no network mocking).
 */
describe("CurrentMarketCard — The Act", () => {
  beforeEach(() => {
    useWsStore.getState().reset();
  });

  it("renders the live market, YES odds, and the bet when newest entry is a BET", () => {
    render(<CurrentMarketCard />);
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
            market_id: "Sinner def. Alcaraz?",
            edge_pct: 0.04,
            odds_yes: 0.58,
            odds_no: 0.42,
          },
        ],
      } as any);
    });

    expect(screen.getByTestId("act-market")).toHaveTextContent("Sinner def. Alcaraz?");
    // ".58" appears in BOTH the YES odds box and the bet line — assert ≥1 match.
    expect(screen.getAllByText(/\.58/).length).toBeGreaterThan(0);
    expect(screen.getByTestId("act-bet")).toHaveTextContent(/YES.*\$50/);
  });

  it("shows the idle scanning card when the newest entry is NO_BET", () => {
    render(<CurrentMarketCard />);
    act(() => {
      useWsStore.getState().ingest({
        kind: "decision_feed",
        ts: "t",
        seq: 1,
        entries: [
          {
            id: "d1",
            ts: "t",
            action: "NO_BET",
            reasoning: "no_eligible_market",
          },
        ],
      } as any);
    });

    expect(screen.getByTestId("act-idle")).toHaveTextContent(/scanning/i);
  });
});
