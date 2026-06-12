import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import React from "react";

import "./setup";

import ReincarnationShell from "../app/reincarnation/ReincarnationShell";
import RoadmapPage from "../app/roadmap/page";
import {
  validateReincarnation,
  type ReincarnationFixture,
  type ReincarnationIncarnation,
} from "@/lib/load_reincarnation";

/**
 * Phase-2 smoke suite — the GROUNDHOG /reincarnation page (design v2).
 *
 * Repo convention: the async server page is never rendered here — the CLIENT
 * shell carries all markup/testids and renders against inline fixtures; the
 * loader's pure validator gets its own unit tests; the roadmap cross-link is
 * asserted by rendering the sync RoadmapPage directly.
 */

const weights = {
  w_r: 0.5,
  w_s: 0.5,
  alpha: [1 / 3, 1 / 3, 1 / 3],
  beta: [0.5, 0.5],
  rho: 0.5,
};

function inc(
  k: number,
  opts: Partial<ReincarnationIncarnation> = {},
): Record<string, unknown> {
  const died = opts.died ?? true;
  const pnl = opts.pnl_at_death ?? 40 + k * 10;
  return {
    incarnation: k,
    died,
    pnl_at_death: pnl,
    scored_pnl: died ? 0 : pnl,
    markets_seen: 100 + 30 * k,
    progress_pct: opts.progress_pct ?? Math.min(100, 8 * k),
    settled: 50 + 10 * k,
    bets: 60 + 10 * k,
    win_rate: 0.78,
    start_weights: weights,
    terminal_weights: weights,
    rebirth_note: opts.rebirth_note ?? null,
    prayer: opts.prayer ?? null,
    advisor: opts.advisor ?? { called: false, proposals: 0, applied: 0 },
    carry: { ema_keys: ["rho_quality"], ema_size: 1 },
    ...(opts.curve !== undefined ? { curve: opts.curve } : {}),
  };
}

function buildFixture(
  overrides: Partial<Record<string, unknown>> = {},
): ReincarnationFixture {
  const incarnations = (overrides.incarnations as unknown[]) ?? [
    inc(1, { curve: [{ i: 0, cum_pnl: 0 }, { i: 50, cum_pnl: 42 }] }),
    inc(2, {
      rebirth_note: "trim alpha_2, breathe smaller",
      prayer: "let me see my own breath before I bet",
    }),
    inc(3, { died: false, progress_pct: 100, pnl_at_death: 188 }),
  ];
  const survived = (overrides.survived as boolean | undefined) ?? true;
  return validateReincarnation({
    experiment: "reincarnation",
    design: "groundhog_day",
    schema_version: 2,
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
      train_rows: 3431,
      holdout_rows: 1471,
      train_fraction: 0.7,
      train_end_ts: "2025-08-01T00:00:00+00:00",
      holdout_start_ts: "2025-08-01T06:00:00+00:00",
    },
    knobs: {
      max_incarnations: 120,
      fragile_max_breath_risk_pct: 0.95,
      loss_multiplier: 5,
      initial_breath: 35,
      initial_bankroll_usd: 100,
      holdout_max_lives: 12,
    },
    scoring:
      "dead incarnations score zero; the headline belongs to the surviving life only",
    survived,
    surviving_incarnation: survived ? 3 : null,
    headline_pnl: survived ? 188 : 0,
    rebirth: {
      expected: 0,
      calls: 0,
      productive: 0,
      empty_or_failed: 0,
      proposals: 0,
      applied: 0,
    },
    incarnations,
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

function cappedFixture(): ReincarnationFixture {
  return buildFixture({
    survived: false,
    surviving_incarnation: null,
    headline_pnl: 0,
    incarnations: [inc(1), inc(2)],
  });
}

describe("validateReincarnation (groundhog v2)", () => {
  it("accepts a well-formed survived artifact and a capped one", () => {
    expect(buildFixture().survived).toBe(true);
    expect(cappedFixture().survived).toBe(false);
  });

  it("rejects wrong design/schema and legacy physics", () => {
    expect(() => validateReincarnation({ experiment: "nope" })).toThrowError();
    const good = buildFixture() as unknown as Record<string, unknown>;
    expect(() =>
      validateReincarnation({ ...good, design: "passes" }),
    ).toThrowError();
    expect(() =>
      validateReincarnation({ ...good, schema_version: 1 }),
    ).toThrowError(/schema_version/);
    expect(() =>
      validateReincarnation({
        ...good,
        physics: { side_correct_pricing: false, value_betting: true },
      }),
    ).toThrowError();
  });

  it("enforces the permadeath-economics cross-field invariants", () => {
    const good = buildFixture() as unknown as Record<string, unknown>;
    // A dead row with money kept.
    expect(() =>
      validateReincarnation({
        ...good,
        incarnations: [inc(1), { ...inc(2), scored_pnl: 99 }, inc(3, { died: false, progress_pct: 100, pnl_at_death: 188 })],
      }),
    ).toThrowError(/scoring rule/);
    // Capped artifact claiming profit.
    expect(() =>
      validateReincarnation({
        ...good,
        survived: false,
        surviving_incarnation: null,
        headline_pnl: 50,
        incarnations: [inc(1), inc(2)],
      }),
    ).toThrowError(/headline 0/);
    // Survivor pointer at a dead row.
    expect(() =>
      validateReincarnation({
        ...good,
        surviving_incarnation: 1,
        headline_pnl: 0,
      }),
    ).toThrowError(/dead row/);
    // curve is OPTIONAL — a fixture whose incarnations all omit it passes.
    const noCurves = buildFixture({
      incarnations: [
        inc(1),
        inc(2),
        inc(3, { died: false, progress_pct: 100, pnl_at_death: 188 }),
      ],
    });
    expect(noCurves.incarnations[0]?.curve).toBeUndefined();
  });
});

describe("ReincarnationShell — groundhog page body", () => {
  it("renders the abyss route shell with the groundhog hero", () => {
    render(<ReincarnationShell numerical={buildFixture()} ai={null} />);
    const root = screen.getByTestId("reincarnation-route");
    expect(root.className).toContain("abyss");
    expect(
      screen.getByText(/die\. remember\. restart at bet/i),
    ).toBeInTheDocument();
  });

  it("renders the survival frontier and the incarnation log", () => {
    render(<ReincarnationShell numerical={buildFixture()} ai={null} />);
    expect(screen.getByTestId("reincarnation-frontier")).toBeInTheDocument();
    for (const k of [1, 2, 3]) {
      expect(screen.getByTestId(`reincarnation-inc-${k}`)).toBeInTheDocument();
    }
    // Dead incarnations show forfeited money: held struck-through, scored $0.
    const dead = screen.getByTestId("reincarnation-inc-1");
    expect(within(dead).getByText("$50").className).toContain("line-through");
    expect(within(dead).getByText("$0")).toBeInTheDocument();
    // The rebirth note surfaces.
    expect(screen.getByText(/trim alpha_2/)).toBeInTheDocument();
    // The dying wish surfaces (A6) on its incarnation row.
    const prayer = screen.getByTestId("reincarnation-prayer-2");
    expect(prayer.textContent).toMatch(/see my own breath/);
  });

  it("renders the survived verdict with the headline pnl", () => {
    render(<ReincarnationShell numerical={buildFixture()} ai={null} />);
    const verdict = screen.getByTestId("reincarnation-verdict");
    expect(within(verdict).getByText("$188")).toBeInTheDocument();
  });

  it("renders the capped verdict honestly ($0 headline)", () => {
    render(<ReincarnationShell numerical={cappedFixture()} ai={null} />);
    const verdict = screen.getByTestId("reincarnation-verdict");
    expect(within(verdict).getByText("$0")).toBeInTheDocument();
    expect(verdict.textContent).toMatch(/no life survived/i);
  });

  it("renders cold-start panel, honest notes, and back-links", () => {
    render(<ReincarnationShell numerical={buildFixture()} ai={null} />);
    expect(screen.getByTestId("reincarnation-coldstart")).toBeInTheDocument();
    const honest = screen.getByTestId("reincarnation-honest");
    expect(honest.textContent).toMatch(/permadeath economics/i);
    expect(honest.textContent).toMatch(/memorization/i);
    expect(honest.textContent).toMatch(/cold-start/i);
    expect(honest.textContent).toMatch(/design history/i);
    expect(honest.textContent).toMatch(/prayers are recorded, never granted/i);
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
