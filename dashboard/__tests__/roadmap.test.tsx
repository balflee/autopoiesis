import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import React from "react";

import "./setup";

import RoadmapPage from "../app/roadmap/page";

/**
 * Phase C smoke test — the Roadmap landing.
 *
 * Asserts the redesigned lifeline renders its hero title, all four
 * lifecycle stage names, the breath-waveform motif, and the ACTIVE
 * stage's pulsing chip. Mirrors the existing component-render pattern
 * (RTL + `import "./setup"` for the jest-dom matchers + cleanup hook).
 *
 * The page is a server component, but it has no async / server-only
 * dependencies (just <Link> + pure JSX), so it renders synchronously
 * under jsdom.
 */
describe("RoadmapPage — landing smoke", () => {
  it("renders the AUTOPOIESIS hero and all four stage names", () => {
    render(<RoadmapPage />);

    expect(screen.getByRole("heading", { name: "AUTOPOIESIS" })).toBeInTheDocument();

    for (const name of ["BACKTEST", "L5 · LEARNING", "MOCK BET", "LIVEBET"]) {
      expect(screen.getByText(name)).toBeInTheDocument();
    }
  });

  it("renders the breath-waveform motif and the ACTIVE status chip", () => {
    render(<RoadmapPage />);

    expect(screen.getAllByTestId("breath-waveform").length).toBeGreaterThan(0);
    // Exactly one stage (L5 · LEARNING) is ACTIVE and pulses.
    expect(screen.getByTestId("status-chip-ACTIVE")).toBeInTheDocument();
  });

  it("links the DONE stage to /backtest and leaves COMING_SOON unlinked", () => {
    render(<RoadmapPage />);

    const backtestLink = screen
      .getByText("BACKTEST")
      .closest("a") as HTMLAnchorElement | null;
    expect(backtestLink).not.toBeNull();
    expect(backtestLink?.getAttribute("href")).toBe("/backtest");

    // LIVEBET is COMING_SOON → no anchor wrapper.
    expect(screen.getByText("LIVEBET").closest("a")).toBeNull();
  });
});

describe("RoadmapPage — MOCK BET L5 gate (F1)", () => {
  const ORIGINAL_ENV = process.env.NEXT_PUBLIC_L5_COMPLETE;

  afterEach(() => {
    if (ORIGINAL_ENV === undefined) {
      delete process.env.NEXT_PUBLIC_L5_COMPLETE;
    } else {
      process.env.NEXT_PUBLIC_L5_COMPLETE = ORIGINAL_ENV;
    }
  });

  it("when L5 is NOT complete: MOCK BET is locked and NOT a link (cannot 404)", () => {
    delete process.env.NEXT_PUBLIC_L5_COMPLETE;
    render(<RoadmapPage />);

    // Locked → no anchor wrapper, so it can never navigate to the
    // not-yet-built /mock route.
    expect(screen.getByText("MOCK BET").closest("a")).toBeNull();
    // Chip reflects the locked lifecycle state.
    expect(screen.getByTestId("status-chip-LOCKED")).toBeInTheDocument();
  });

  it("when NEXT_PUBLIC_L5_COMPLETE is set: MOCK BET becomes an ACTIVE link to /living", () => {
    process.env.NEXT_PUBLIC_L5_COMPLETE = "true";
    render(<RoadmapPage />);

    const mockLink = screen
      .getByText("MOCK BET")
      .closest("a") as HTMLAnchorElement | null;
    expect(mockLink).not.toBeNull();
    // Links to the LIVE Living Stage showpiece (NOT MOCK_ROUTE /mock — that
    // constant still gates the standalone /mock page).
    expect(mockLink?.getAttribute("href")).toBe("/living");

    // The unlock note ("unlocks when L5 completes") is gone once unlocked.
    expect(screen.queryByText(/unlocks when l5 completes/i)).toBeNull();
    // No locked chip remains.
    expect(screen.queryByTestId("status-chip-LOCKED")).toBeNull();
  });
});
