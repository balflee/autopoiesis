import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, beforeEach } from "vitest";
import React from "react";

import "../setup";

import { VitalsPanel } from "../../components/VitalsPanel";
import { useWsStore } from "../../lib/wsStore";
import type { VitalsMessage } from "../../lib/types";

/**
 * T-D-002 VitalsPanel acceptance suite.
 *
 * Asserts the 'WS not yet connected' loading state and the three
 * primary projections (BREATH %, bankroll, phase badge) against
 * injected mock frames. Mocks land via the Zustand store seam — no
 * network involved.
 */

const baseFrame = (overrides: Partial<VitalsMessage["payload"]> = {}): VitalsMessage => ({
  kind: "vitals",
  ts: "2026-05-21T12:00:00Z",
  seq: 1,
  payload: {
    breath: 78,
    bankroll: 142.5,
    countdown_s: 95,
    gas_per_min: 0.12,
    phase: "PHASE_2_APPRENTICE",
    ...overrides,
  },
});

describe("VitalsPanel — loading + projection", () => {
  beforeEach(() => {
    useWsStore.getState().reset();
  });

  it("renders the loading skeleton when no vitals frame has been received yet", () => {
    render(<VitalsPanel />);
    const panel = screen.getByTestId("vitals-panel");
    expect(panel.getAttribute("data-loading")).toBe("true");
    expect(panel).toHaveTextContent(/waiting for agent stream/i);
  });

  it("projects BREATH %, bankroll, and phase label after ingesting a vitals frame", () => {
    render(<VitalsPanel />);
    act(() => {
      useWsStore.getState().ingest(baseFrame());
    });
    expect(screen.getByTestId("vitals-panel").getAttribute("data-loading")).toBeNull();
    expect(screen.getByTestId("vitals-breath-value")).toHaveTextContent("78 / 100");
    expect(screen.getByTestId("vitals-bankroll-value")).toHaveTextContent("$142.50");
    expect(screen.getByTestId("vitals-phase-badge")).toHaveTextContent(/apprentice/i);

    // 1:35 = 95 seconds
    expect(screen.getByTestId("vitals-countdown")).toHaveTextContent("1:35");
    expect(screen.getByTestId("vitals-gas")).toHaveTextContent("0.12");
  });

  it("flips the BREATH bar colour to LOSS once energy drops to ≤10 %", () => {
    render(<VitalsPanel />);
    act(() => {
      useWsStore.getState().ingest(baseFrame({ breath: 8 }));
    });
    const fill = screen.getByTestId("vitals-breath-fill");
    // jsdom serialises inline hex colors to rgb() — assert against the
    // canonical rgb form so we are robust to that conversion.
    expect(fill.getAttribute("style")).toMatch(/rgb\(230,\s*57,\s*70\)/i);
  });

  it("reflects later Phase 4 updates without remounting", () => {
    render(<VitalsPanel />);
    act(() => {
      // T-D-003: fixed pre-existing `Partial<payload>` typing — the
      // `seq` override belongs on the envelope, not the payload.
      const f1 = baseFrame({ phase: "PHASE_3_MASTER" });
      useWsStore.getState().ingest({ ...f1, seq: 1 });
    });
    expect(screen.getByTestId("vitals-phase-badge")).toHaveTextContent(/mastery/i);
    act(() => {
      const f2 = baseFrame({ phase: "PHASE_4_TERMINAL", breath: 4 });
      useWsStore.getState().ingest({ ...f2, seq: 2 });
    });
    expect(screen.getByTestId("vitals-phase-badge")).toHaveTextContent(/terminal/i);
  });
});
