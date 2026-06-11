import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import React from "react";

import "./setup";

import BacktestRoute from "../app/backtest/page";
import {
  SIGNAL_SLOT_LABEL,
  STATIC_SWEEP,
} from "@/lib/load_static_sweep";

/**
 * Phase D smoke test — the restyled /backtest page (T-D-002 / D2).
 *
 * The page was rebuilt on top of the validated `load_static_sweep` loader
 * and the abyssal design system. This suite asserts the page renders the
 * four story panels driven by the REAL backtest sweep:
 *
 *   - the optimal SEED config (weights as labeled bars + the sizing knobs)
 *   - the robust-frontier table (10 rows, all metric columns)
 *   - the methodology story (real signals, 65.7% coverage, $5 cap, min-bets)
 *   - the bet-detail drill-down (sample resolved markets with 5 signals)
 *
 * Mirrors the roadmap smoke pattern: RTL + `import "./setup"` for the
 * jest-dom matchers + cleanup. The page is a client component but has no
 * async / server-only dependencies (the fixture is a static ES-module JSON
 * import), so it renders synchronously under jsdom.
 */
describe("BacktestRoute — abyssal sweep smoke", () => {
  it("renders under the .abyss design scope with the page hero", () => {
    render(<BacktestRoute />);
    const page = screen.getByTestId("backtest-route");
    expect(page).toBeInTheDocument();
    expect(page).toHaveClass("abyss");
    expect(
      screen.getByRole("heading", { name: /backtest/i }),
    ).toBeInTheDocument();
  });

  it("shows the optimal seed headline metrics from the real sweep", () => {
    render(<BacktestRoute />);
    const hero = screen.getByTestId("backtest-route");
    // Sharpe 0.649, 81.5% win, 65 bets — the headline telemetry numbers.
    expect(within(hero).getAllByText(/0\.649/).length).toBeGreaterThan(0);
    expect(within(hero).getAllByText(/81\.5\s*%/).length).toBeGreaterThan(0);
    expect(within(hero).getAllByText(/^65$/).length).toBeGreaterThan(0);
  });

  it("renders a labeled weight bar for every fusion weight", () => {
    render(<BacktestRoute />);
    // w_r, w_s, alpha[3], beta[2], rho → 7 weight bars.
    for (const key of [
      "w_r",
      "w_s",
      "alpha_1",
      "alpha_2",
      "alpha_3",
      "beta_1",
      "beta_2",
      "rho",
    ]) {
      expect(screen.getByTestId(`weight-bar-${key}`)).toBeInTheDocument();
    }
    // The slot-name repurpose note must surface the alpha → signal mapping.
    expect(screen.getByTestId("slot-repurpose-note")).toBeInTheDocument();
  });

  it("renders the robust frontier table with all 10 ranked rows", () => {
    render(<BacktestRoute />);
    const table = screen.getByTestId("frontier-table");
    expect(table).toBeInTheDocument();
    for (const row of STATIC_SWEEP.frontier) {
      expect(
        within(table).getByTestId(`frontier-row-${row.rank}`),
      ).toBeInTheDocument();
    }
  });

  it("surfaces the methodology story numbers (coverage, bankroll cap, min-bets gate)", () => {
    render(<BacktestRoute />);
    const story = screen.getByTestId("methodology-panel");
    expect(within(story).getAllByText(/65\.7\s*%/).length).toBeGreaterThan(0);
    // The $5 breath-bankroll cap and the ≥50-bets robustness gate.
    expect(within(story).getAllByText(/\$5/).length).toBeGreaterThan(0);
    expect(within(story).getAllByText(/≥50/).length).toBeGreaterThan(0);
  });

  it("renders a bet drill-down row per sample bet with its 5 signal scores", () => {
    render(<BacktestRoute />);
    const drill = screen.getByTestId("bet-drilldown");
    expect(drill).toBeInTheDocument();
    expect(
      within(drill).getAllByTestId(/^bet-row-/).length,
    ).toBe(STATIC_SWEEP.sample_bets.length);

    // The first sample bet's players + all five labeled signal scores show up.
    const first = STATIC_SWEEP.sample_bets[0]!;
    const firstRow = within(drill).getByTestId(`bet-row-${first.market_id}`);
    expect(
      within(firstRow).getByText(new RegExp(first.players[0], "i")),
    ).toBeInTheDocument();
    for (const label of Object.values(SIGNAL_SLOT_LABEL)) {
      expect(within(firstRow).getAllByText(label).length).toBeGreaterThan(0);
    }
  });

  it("keeps a link back to the roadmap landing", () => {
    render(<BacktestRoute />);
    const back = screen.getByTestId("backtest-back-link");
    expect(back.getAttribute("href")).toBe("/roadmap");
  });
});

/**
 * G1 — the shared lifeline StageShell renders the backtest stage's chrome:
 * the top-left ◂ lifeline back-link to /roadmap, the top-right INFANCY stage
 * label, and the footer's forward cross-link to the next stage (/survival).
 * Byte-visually-identical to the previously-inlined shell.
 */
describe("BacktestRoute — shared StageShell nav (G1)", () => {
  it("renders the infancy stage label + ◂ lifeline back-link to /roadmap", () => {
    render(<BacktestRoute />);
    expect(screen.getByText("infancy · the seed policy")).toBeInTheDocument();
    const back = screen.getByTestId("backtest-back-link");
    expect(back).toHaveTextContent("◂ lifeline");
    expect(back.getAttribute("href")).toBe("/roadmap");
  });

  it("forward-links to the next lifeline stage (/survival)", () => {
    render(<BacktestRoute />);
    const next = screen
      .getByText(/next · learning to survive/i)
      .closest("a") as HTMLAnchorElement | null;
    expect(next).not.toBeNull();
    expect(next?.getAttribute("href")).toBe("/survival");
  });
});
