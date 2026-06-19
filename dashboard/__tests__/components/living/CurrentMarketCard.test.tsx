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
 *   3. STICKY: an OPEN bet keeps the card lit even when the newest decision is
 *      a NO_BET (the agent bets selectively — the held position should show).
 *   4. A SETTLED bet (no open positions) falls back to "scanning".
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

  it("stays lit on the latest OPEN bet even when the newest decision is NO_BET", () => {
    render(<CurrentMarketCard />);
    // An open (PENDING) bet at t1...
    act(() => {
      useWsStore.getState().ingest({
        kind: "decision_feed",
        ts: "t",
        seq: 1,
        entries: [
          {
            id: "bet1",
            ts: "2026-06-19T07:00:00Z",
            action: "BET",
            side: "YES",
            size_usd: 2.74,
            market_id: "2585700",
            edge_pct: 0.227,
            odds_yes: 0.41,
            odds_no: 0.59,
            result: "PENDING",
          },
        ],
      } as any);
    });
    // ...then a NEWER NO_BET tick at t2 (the agent abstained on the next market).
    act(() => {
      useWsStore.getState().ingest({
        kind: "decision_feed",
        ts: "t",
        seq: 2,
        entries: [
          {
            id: "nobet1",
            ts: "2026-06-19T07:01:00Z",
            action: "NO_BET",
            reasoning: "size_below_min_bet:0.3",
          },
        ],
      } as any);
    });

    // The newest decision is the NO_BET, but the held position keeps the card lit.
    expect(screen.getByTestId("act-market")).toHaveTextContent("2585700");
    expect(screen.queryByTestId("act-idle")).toBeNull();
  });

  it("falls back to scanning once the only bet has SETTLED (no open positions)", () => {
    render(<CurrentMarketCard />);
    act(() => {
      useWsStore.getState().ingest({
        kind: "decision_feed",
        ts: "t",
        seq: 1,
        entries: [
          {
            id: "bet1",
            ts: "2026-06-19T07:00:00Z",
            action: "BET",
            side: "YES",
            size_usd: 2.74,
            market_id: "2585700",
            result: "WIN",
            pnl_usd: 1.8,
          },
        ],
      } as any);
    });

    expect(screen.getByTestId("act-idle")).toHaveTextContent(/scanning/i);
    expect(screen.queryByTestId("act-market")).toBeNull();
  });
});
