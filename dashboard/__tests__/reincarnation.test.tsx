import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import React from "react";

import "./setup";

import ReincarnationShell from "../app/reincarnation/ReincarnationShell";
import RoadmapPage from "../app/roadmap/page";
import {
  validateReincarnation,
  type ReincarnationFixture,
} from "@/lib/load_reincarnation";

/**
 * Phase-2 smoke suite — the /reincarnation experiment page.
 *
 * Repo convention: the async server page is NOT rendered here (it only loads
 * fixtures and hands them to the client shell) — the CLIENT shell carries all
 * markup/testids and is rendered directly against an inline fixture, the
 * loader's pure validator gets its own unit tests, and the roadmap cross-link
 * is asserted by rendering the sync RoadmapPage directly (same as docs tests).
 */

function buildFixture(
  overrides: Partial<ReincarnationFixture> = {},
): ReincarnationFixture {
  const weights = {
    w_r: 0.5,
    w_s: 0.5,
    alpha: [1 / 3, 1 / 3, 1 / 3],
    beta: [0.5, 0.5],
    rho: 0.5,
  };
  const pass = (i: number, pnl: number, note: string | null) => ({
    pass: i,
    summary: {
      pnl,
      deaths: 4 - i,
      lives: 5 - i,
      settled: 100 + i,
      coverage_pct: 25.5,
      win_rate: 0.61,
    },
    per_life_pnls: [pnl / 2, pnl / 2],
    start_weights: weights,
    terminal_weights: weights,
    curve: [
      { i: 0, cum_pnl: 0 },
      { i: 50, cum_pnl: pnl / 2 },
      { i: 100, cum_pnl: pnl },
    ],
    rebirth_note: note,
    carry: { ema_keys: ["rho_quality"], ema_size: 1 },
  });
  return validateReincarnation({
    experiment: "reincarnation",
    provider: "numerical",
    physics: {
      side_correct_pricing: true,
      value_betting: true,
      entry_price_floor: 0.05,
      max_bet_pnl_usd: 100,
      effective_entry_price_floor: 0.05,
      min_effective_entry_price: 0.07,
      min_edge: 0.0349,
      kappa: 0.4921,
    },
    split: {
      train_rows: 2400,
      holdout_rows: 1029,
      train_fraction: 0.7,
      train_end_ts: "2025-08-01T00:00:00+00:00",
      holdout_start_ts: "2025-08-01T06:00:00+00:00",
    },
    knobs: {
      passes: 3,
      fragile_max_breath_risk_pct: 0.95,
      loss_multiplier: 5,
      initial_breath: 35,
      max_lives: 12,
    },
    passes: [pass(1, 120, null), pass(2, 260, "trim alpha_2"), pass(3, 410, null)],
    holdout: {
      summary: {
        pnl: 88,
        deaths: 2,
        lives: 3,
        settled: 60,
        coverage_pct: 18.2,
        win_rate: 0.58,
        learning_enabled: false,
      },
      start_weights: weights,
      curve: [
        { i: 0, cum_pnl: 0 },
        { i: 60, cum_pnl: 88 },
      ],
      baselines: { static: 40, random: -12, always_favorite: -60 },
    },
    ...overrides,
  });
}

describe("validateReincarnation", () => {
  it("accepts a well-formed artifact", () => {
    const f = buildFixture();
    expect(f.experiment).toBe("reincarnation");
    expect(f.passes).toHaveLength(3);
    expect(f.holdout.summary.learning_enabled).toBe(false);
  });

  it("rejects a wrong experiment tag, empty passes, and legacy physics", () => {
    expect(() =>
      validateReincarnation({ experiment: "nope" }),
    ).toThrowError();
    const good = buildFixture() as unknown as Record<string, unknown>;
    expect(() =>
      validateReincarnation({ ...good, passes: [] }),
    ).toThrowError();
    expect(() =>
      validateReincarnation({
        ...good,
        physics: { side_correct_pricing: false, value_betting: true },
      }),
    ).toThrowError();
  });
});

describe("ReincarnationShell — Phase-2 page body", () => {
  it("renders the abyss-scoped route shell with the hero", () => {
    render(<ReincarnationShell numerical={buildFixture()} ai={null} />);
    const root = screen.getByTestId("reincarnation-route");
    expect(root).toBeInTheDocument();
    expect(root.className).toContain("abyss");
    expect(
      screen.getByText(/the same season, lived three times/i),
    ).toBeInTheDocument();
  });

  it("renders one metric card per pass", () => {
    render(<ReincarnationShell numerical={buildFixture()} ai={null} />);
    for (const i of [1, 2, 3]) {
      const card = screen.getByTestId(`reincarnation-pass-${i}`);
      expect(card).toBeInTheDocument();
    }
    // The rebirth note from pass 2 surfaces.
    expect(screen.getByText(/trim alpha_2/)).toBeInTheDocument();
  });

  it("renders the overlay chart, cold-start verdict, and honest notes", () => {
    render(<ReincarnationShell numerical={buildFixture()} ai={null} />);
    expect(screen.getByTestId("reincarnation-chart")).toBeInTheDocument();
    const cold = screen.getByTestId("reincarnation-coldstart");
    expect(within(cold).getAllByText(/frozen/i).length).toBeGreaterThan(0);
    const honest = screen.getByTestId("reincarnation-honest");
    expect(honest.textContent).toMatch(/memorization/i);
    expect(honest.textContent).toMatch(/cold-start/i);
  });

  it("links back to /survival (Phase 1) and /docs", () => {
    render(<ReincarnationShell numerical={buildFixture()} ai={null} />);
    const links = screen
      .getAllByRole("link")
      .map((a) => a.getAttribute("href") ?? "");
    expect(links).toContain("/survival");
    expect(links).toContain("/docs");
  });
});

describe("roadmap — Phase-2 cross-link", () => {
  it("links to /reincarnation from the landing hero", () => {
    render(<RoadmapPage />);
    const link = screen.getByTestId("roadmap-reincarnation-link");
    expect(link.getAttribute("href")).toBe("/reincarnation");
  });
});
