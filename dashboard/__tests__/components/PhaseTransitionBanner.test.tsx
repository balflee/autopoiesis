import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, beforeEach } from "vitest";
import React from "react";

import "../setup";

import { PhaseTransitionBanner } from "../../components/PhaseTransitionBanner";
import { useWsStore } from "../../lib/wsStore";
import type { PhaseTransitionMessage } from "../../lib/types";

describe("PhaseTransitionBanner", () => {
  beforeEach(() => {
    useWsStore.getState().reset();
  });

  it("returns null until a phase_transition frame lands", () => {
    const { container } = render(<PhaseTransitionBanner />);
    expect(container.firstChild).toBeNull();
  });

  it("renders the headline + reason on a P1→P2 transition", () => {
    render(<PhaseTransitionBanner />);
    const frame: PhaseTransitionMessage = {
      kind: "phase_transition",
      ts: "2026-05-21T12:00:00Z",
      seq: 1,
      payload: {
        from: "PHASE_1_INFANCY",
        to: "PHASE_2_APPRENTICE",
        reason: "β₁ unfrozen at Phase 2 boundary",
      },
    };
    act(() => {
      useWsStore.getState().ingest(frame);
    });
    const banner = screen.getByTestId("phase-transition-banner");
    expect(banner.getAttribute("data-from")).toBe("PHASE_1_INFANCY");
    expect(banner.getAttribute("data-to")).toBe("PHASE_2_APPRENTICE");
    expect(screen.getByTestId("phase-transition-banner-headline")).toHaveTextContent(
      /Phase 1 · Infancy → Phase 2 · Apprenticeship/,
    );
    expect(screen.getByTestId("phase-transition-banner-reason")).toHaveTextContent(
      /β₁ unfrozen/,
    );
  });

  it("dismisses the banner when the X is clicked", () => {
    render(<PhaseTransitionBanner />);
    const frame: PhaseTransitionMessage = {
      kind: "phase_transition",
      ts: "2026-05-21T12:00:00Z",
      seq: 1,
      payload: { from: "PHASE_1_INFANCY", to: "PHASE_2_APPRENTICE" },
    };
    act(() => {
      useWsStore.getState().ingest(frame);
    });
    expect(screen.getByTestId("phase-transition-banner")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("phase-transition-banner-dismiss"));
    expect(screen.queryByTestId("phase-transition-banner")).toBeNull();
  });
});
