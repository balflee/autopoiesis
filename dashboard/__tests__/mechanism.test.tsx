import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import React from "react";

import "./setup";

import MechanismPage from "../app/mechanism/page";
import RoadmapPage from "../app/roadmap/page";

/**
 * Smoke test — the /mechanism hub explainer (project "how it works" page).
 *
 * The page is a content-rich, static server component (just <Link> + pure JSX,
 * no async / server-only deps), so it renders synchronously under jsdom.
 * Mirrors the roadmap/backtest smoke pattern: RTL + `import "./setup"` for the
 * jest-dom matchers + cleanup. Asserts the page renders under the .abyss scope,
 * carries its hero + the load-bearing facts, threads its nine sections, and is
 * cross-linked from the roadmap landing.
 */
describe("MechanismPage — hub explainer smoke", () => {
  it("renders under the .abyss design scope with the hero title", () => {
    render(<MechanismPage />);
    const page = screen.getByTestId("mechanism-route");
    expect(page).toBeInTheDocument();
    expect(page).toHaveClass("abyss");
    expect(
      screen.getByRole("heading", { name: /the mechanism/i }),
    ).toBeInTheDocument();
  });

  it("names the competition arena (Arbitrum Open House London)", () => {
    render(<MechanismPage />);
    const arena = screen.getByTestId("mechanism-arena");
    expect(
      within(arena).getByText(/Arbitrum Open House London/i),
    ).toBeInTheDocument();
    expect(within(arena).getByText(/Robinhood Chain/i)).toBeInTheDocument();
  });

  it("surfaces the seed headline stats (Sharpe 0.649, 81.5% win)", () => {
    render(<MechanismPage />);
    const fusion = screen.getByTestId("mechanism-fusion");
    expect(within(fusion).getByText(/Sharpe/i)).toBeInTheDocument();
    expect(within(fusion).getByText(/0\.649/)).toBeInTheDocument();
    expect(within(fusion).getByText(/81\.5\s*%/)).toBeInTheDocument();
  });

  it("explains the L6 learning loop with Gemini + the StrategyAdvisor", () => {
    render(<MechanismPage />);
    const learning = screen.getByTestId("mechanism-learning");
    expect(within(learning).getByText(/Gemini/i)).toBeInTheDocument();
    expect(within(learning).getByText(/StrategyAdvisor/i)).toBeInTheDocument();
  });

  it("describes the BREATH economy and permadeath → Tombstone NFT", () => {
    render(<MechanismPage />);
    const breath = screen.getByTestId("mechanism-breath");
    expect(within(breath).getByText(/Tombstone NFT/i)).toBeInTheDocument();
    expect(within(breath).getAllByText(/BREATH/).length).toBeGreaterThan(0);
  });

  it("renders all nine threaded sections and the breath-waveform motif", () => {
    render(<MechanismPage />);
    for (const id of [
      "mechanism-arena",
      "mechanism-data",
      "mechanism-engines",
      "mechanism-fusion",
      "mechanism-lifecycle",
      "mechanism-breath",
      "mechanism-learning",
      "mechanism-params",
      "mechanism-stack",
    ]) {
      expect(screen.getByTestId(id)).toBeInTheDocument();
    }
    expect(screen.getAllByTestId("breath-waveform").length).toBeGreaterThan(0);
  });

  it("keeps a ◂ lifeline back-link to /roadmap", () => {
    render(<MechanismPage />);
    const back = screen.getByTestId("mechanism-back-link");
    expect(back).toHaveTextContent("◂ lifeline");
    expect(back.getAttribute("href")).toBe("/roadmap");
  });
});

describe("RoadmapPage — links to /mechanism", () => {
  it("renders a 'how it works' link to /mechanism", () => {
    render(<RoadmapPage />);
    const link = screen.getByTestId("roadmap-mechanism-link");
    expect(link.getAttribute("href")).toBe("/mechanism");
    expect(link).toHaveTextContent(/how it works/i);
  });
});
