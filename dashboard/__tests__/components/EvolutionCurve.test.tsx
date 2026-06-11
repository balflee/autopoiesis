import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, beforeEach } from "vitest";
import React from "react";

import "../setup";

import { EvolutionCurve } from "../../components/EvolutionCurve";
import { useWsStore } from "../../lib/wsStore";
import type {
  DecisionFeedMessage,
  PhaseTransitionMessage,
  WeightsUpdatedMessage,
} from "../../lib/types";

/**
 * EvolutionCurve acceptance suite.
 *
 * Covers the three demo-critical surfaces:
 *   1. Empty state — "waiting for first settled trade…"
 *   2. Cumulative win-rate readout after WIN/LOSS rows settle
 *   3. β₁ activation marker once a weights frame with beta > 0 lands
 *   4. Phase-transition marker once a phase_transition frame lands
 */

describe("EvolutionCurve", () => {
  beforeEach(() => {
    useWsStore.getState().reset();
  });

  it("shows the empty-state caption when no data is available", () => {
    render(<EvolutionCurve />);
    const root = screen.getByTestId("evolution-curve");
    expect(root.getAttribute("data-loading")).toBe("true");
    expect(screen.getByTestId("evolution-curve-empty")).toBeInTheDocument();
  });

  it("computes cumulative win rate after WIN/LOSS rows are ingested", () => {
    render(<EvolutionCurve />);

    const feedFrame: DecisionFeedMessage = {
      kind: "decision_feed",
      ts: "2026-05-21T12:05:00Z",
      seq: 1,
      entries: [
        {
          id: "d1",
          ts: "2026-05-21T12:00:00Z",
          action: "BET",
          side: "LAL ML",
          size_usd: 50,
          result: "WIN",
          pnl_usd: 47.6,
        },
        {
          id: "d2",
          ts: "2026-05-21T12:02:00Z",
          action: "BET",
          side: "BOS ML",
          size_usd: 50,
          result: "LOSS",
          pnl_usd: -50,
        },
        {
          id: "d3",
          ts: "2026-05-21T12:04:00Z",
          action: "BET",
          side: "GSW ML",
          size_usd: 50,
          result: "WIN",
          pnl_usd: 47.6,
        },
      ],
    };
    act(() => {
      useWsStore.getState().ingest(feedFrame);
    });

    // 2 wins / 3 settled = 66% rounded
    const readout = screen.getByTestId("evolution-curve-win-rate-readout");
    expect(readout).toHaveTextContent(/67%/);
    expect(readout).toHaveTextContent(/2–1/);
    // No longer loading once any settled data exists.
    expect(screen.getByTestId("evolution-curve").getAttribute("data-loading")).toBe(
      "false",
    );
  });

  it("renders the β₁ activation marker when weights with beta > 0 land", () => {
    render(<EvolutionCurve />);
    const frozenFrame: WeightsUpdatedMessage = {
      kind: "weights_updated",
      ts: "2026-05-21T11:55:00Z",
      seq: 1,
      weights: { w_r: 1, w_s: 0, alpha: 0.5, beta: 0, rho: 0 },
    };
    const unfrozenFrame: WeightsUpdatedMessage = {
      kind: "weights_updated",
      ts: "2026-05-21T12:00:00Z",
      seq: 2,
      weights: { w_r: 0.6, w_s: 0.4, alpha: 0.5, beta: 0.3, rho: 0.1 },
    };
    act(() => {
      useWsStore.getState().ingest(frozenFrame);
      useWsStore.getState().ingest(unfrozenFrame);
    });
    expect(screen.getByTestId("evolution-curve-beta-marker")).toBeInTheDocument();
  });

  it("renders the phase-transition marker when a phase_transition frame lands", () => {
    render(<EvolutionCurve />);
    const ptFrame: PhaseTransitionMessage = {
      kind: "phase_transition",
      ts: "2026-05-21T12:00:00Z",
      seq: 1,
      payload: {
        from: "PHASE_1_INFANCY",
        to: "PHASE_2_APPRENTICE",
        reason: "β₁ unfrozen at Phase 2 boundary",
      },
    };
    // Also ingest a weights point so the axis has a domain.
    const wFrame: WeightsUpdatedMessage = {
      kind: "weights_updated",
      ts: "2026-05-21T11:59:00Z",
      seq: 0,
      weights: { w_r: 0.8, w_s: 0.2, alpha: 0.5, beta: 0.05, rho: 0 },
    };
    act(() => {
      useWsStore.getState().ingest(wFrame);
      useWsStore.getState().ingest(ptFrame);
    });
    expect(screen.getByTestId("evolution-curve-phase-marker")).toBeInTheDocument();
    expect(screen.getByTestId("evolution-curve-phase-marker")).toHaveTextContent(
      "P1→P2",
    );
  });
});
