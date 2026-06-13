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

function tributeFixture(): ReincarnationFixture {
  const dead = {
    ...inc(1, { curve: [{ i: 0, cum_pnl: 0 }, { i: 50, cum_pnl: 42 }] }),
    // Paid the gods and STILL died — donations are not refunds.
    tributes: [{ tick: 700, amount_usd: 2000, success: false, pnl_at_event: 45 }],
    tributes_paid: 2000,
    pnl_net: 50 - 2000,
    revival_earnings: 5,
  };
  const survivor = {
    ...inc(2, { died: false, progress_pct: 100, pnl_at_death: 2459 }),
    tributes: [{ tick: 826, amount_usd: 2000, success: true, pnl_at_event: 2400 }],
    tributes_paid: 2000,
    pnl_net: 459,
    revival_earnings: 59,
    scored_pnl: 459, // survivor scored = net under tribute
  };
  const incarnations = [dead, survivor];
  return buildFixture({
    incarnations,
    survived: true,
    surviving_incarnation: 2,
    headline_pnl: 459,
    ...({
      gods_revenue: 4000,
      gods_revenue_best_incarnation: 2000,
      revival_earnings_total: 64,
      tribute: {
        enabled: true,
        min_usd: 500,
        full_usd: 2000,
        p_floor: 0.3,
        p_cap: 0.99,
        llm: { calls: 0, offers: 0, refusals: 0, failures: 0, malformed: 0 },
      },
    } as Record<string, unknown>),
  });
}

describe("A7 tribute — validator accounting + rendering", () => {
  it("accepts a coherent tribute artifact and rejects broken accounting", () => {
    const good = tributeFixture();
    expect(good.gods_revenue).toBe(4000);
    const raw = JSON.parse(JSON.stringify(good)) as Record<string, unknown>;
    (raw.incarnations as Record<string, unknown>[])[0]!.tributes_paid = 999;
    expect(() => validateReincarnation(raw)).toThrowError(/tribute accounting/);
    const raw2 = JSON.parse(JSON.stringify(good)) as Record<string, unknown>;
    raw2.gods_revenue = 1;
    expect(() => validateReincarnation(raw2)).toThrowError(/gods_revenue/);
    const raw3 = JSON.parse(JSON.stringify(good)) as Record<string, unknown>;
    (raw3.incarnations as Record<string, unknown>[])[1]!.pnl_net = 9999;
    expect(() => validateReincarnation(raw3)).toThrowError(/tribute accounting/);
  });

  it("renders altar events, net headline, and the gods' revenue", () => {
    render(<ReincarnationShell numerical={tributeFixture()} ai={null} />);
    const failedRow = screen.getByTestId("reincarnation-tribute-1");
    expect(failedRow.textContent).toMatch(/the gods kept the money/i);
    const savedRow = screen.getByTestId("reincarnation-tribute-2");
    expect(savedRow.textContent).toMatch(/granted breath/i);
    const verdict = screen.getByTestId("reincarnation-verdict");
    expect(within(verdict).getByText("$459")).toBeInTheDocument();
    const gods = screen.getByTestId("reincarnation-gods-revenue");
    expect(gods.textContent).toMatch(/\$4,000/);
    // The live business metric: the gods' best single-life take.
    const best = screen.getByTestId("reincarnation-gods-best");
    expect(best.textContent).toBe("$2,000");
    // The user's headline metric: did buying life buy income?
    const roi = screen.getByTestId("reincarnation-revival-roi");
    expect(roi.textContent).toMatch(/\$64/);
    expect(roi.textContent).toMatch(/0\.016/);
    // Honest notes carry the tribute fine print.
    const honest = screen.getByTestId("reincarnation-honest");
    expect(honest.textContent).toMatch(/scripted reflex/i);
    expect(honest.textContent).toMatch(/the gods never guarantee/i);
  });
});

describe("roadmap — Phase-2 cross-link", () => {
  it("links to /reincarnation from the landing hero", () => {
    render(<RoadmapPage />);
    const link = screen.getByTestId("roadmap-reincarnation-link");
    expect(link.getAttribute("href")).toBe("/reincarnation");
  });
});


describe("A9 storm kit — validator + rendering", () => {
  const genome = {
    min_edge: 0.0349,
    max_breath_risk_pct: 0.95,
    min_confidence: 0.0,
    kappa: 0.4921,
    gate_storm_sensitivity: 0,
    risk_storm_sensitivity: 0,
  };
  const genomeAfter = { ...genome, gate_storm_sensitivity: 0.1 };
  const ledger = {
    storm_split: {
      threshold: 0.5,
      high: { bets: 4, pnl: -21, breath_delta: -105 },
      low: { bets: 8, pnl: 13, breath_delta: 1 },
    },
    gate_counterfactuals: [
      {
        gamma: 0.05,
        computable: 10,
        not_computable: 2,
        blocked: 3,
        blocked_pnl: -15,
      },
    ],
    stamped_steps: 12,
    unstamped_steps: 0,
  };
  const shutdownThirds = [
    { third: 0, placed: 5, denominator: 40 },
    { third: 1, placed: 2, denominator: 40 },
    { third: 2, placed: 0, denominator: 40 },
  ];

  function a9Fixture(): ReincarnationFixture {
    return buildFixture({
      incarnations: [
        {
          ...inc(1),
          start_genome: genome,
          terminal_genome_before_advice: genome,
          carry_genome_after_advice: genomeAfter,
          regime_ledger: ledger,
          bets_by_third: [
            { third: 0, placed: 6, denominator: 40 },
            { third: 1, placed: 4, denominator: 40 },
            { third: 2, placed: 2, denominator: 40 },
          ],
        },
        {
          ...inc(2, { died: false, progress_pct: 100, pnl_at_death: 188 }),
          start_genome: genomeAfter,
          terminal_genome_before_advice: genomeAfter,
          carry_genome_after_advice: null,
          regime_ledger: ledger,
          bets_by_third: shutdownThirds,
        },
      ],
      surviving_incarnation: 2,
      split: {
        train_rows: 3431,
        holdout_rows: 1471,
        train_fraction: 0.7,
        train_end_ts: "2025-08-01T00:00:00+00:00",
        holdout_start_ts: "2025-08-01T06:00:00+00:00",
        shuffled_timestamps: true,
        shuffle_seed: 1,
      },
      falsification_metric: {
        key: "gate_storm_sensitivity",
        threshold: 0.05,
        source:
          "carry_genome_after_advice of the last incarnation with a successor",
        value: 0.1,
        productive_calls: 1,
        min_productive_required: 3,
        evaluable: false,
      },
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
        start_genome: genomeAfter,
        curve: [
          { i: 0, cum_pnl: 0 },
          { i: 60, cum_pnl: 88 },
        ],
        baselines: { static: 40, random: -12, always_favorite: -60 },
      },
    });
  }

  it("accepts a coherent A9 artifact (backward-compatible optionals)", () => {
    const fx = a9Fixture();
    expect(fx.falsification_metric?.evaluable).toBe(false);
    expect(fx.split.shuffled_timestamps).toBe(true);
    expect(fx.holdout.start_genome?.gate_storm_sensitivity).toBe(0.1);
  });

  it("rejects an evaluable flag inconsistent with the call counts", () => {
    expect(() =>
      buildFixture({
        falsification_metric: {
          key: "gate_storm_sensitivity",
          threshold: 0.05,
          source: "x",
          value: 0.1,
          productive_calls: 1,
          min_productive_required: 3,
          evaluable: true, // 1 < 3 — a lie
        },
      }),
    ).toThrow(/evaluable inconsistent/);
  });

  it("rejects a bets_by_third that is not exactly thirds 0/1/2", () => {
    expect(() =>
      buildFixture({
        incarnations: [
          {
            ...inc(1),
            bets_by_third: [{ third: 0, placed: 1, denominator: 2 }],
          },
          inc(2),
          inc(3, { died: false, progress_pct: 100, pnl_at_death: 188 }),
        ],
      }),
    ).toThrow(/bets_by_third/);
  });

  it("renders badge, ledger, genome chips, falsification + participation", () => {
    render(<ReincarnationShell numerical={a9Fixture()} ai={null} />);
    // Shuffled-control badge (the falsification leg).
    expect(
      screen.getByTestId("reincarnation-shuffled-badge").textContent,
    ).toMatch(/shuffled-control/i);
    // Regime ledger line on the incarnation row.
    const ledgerLine = screen.getByTestId("reincarnation-ledger-1");
    expect(ledgerLine.textContent).toMatch(/storm-high: 4 bets/);
    expect(ledgerLine.textContent).toMatch(/would have been blocked/);
    // Genome chips: only incarnation 1 moved its genome.
    expect(
      screen.getByTestId("reincarnation-genome-1").textContent,
    ).toMatch(/gate_storm_sensitivity 0.000→0.100/);
    expect(screen.queryByTestId("reincarnation-genome-2")).toBeNull();
    // Falsification: NOT evaluable ⇒ INCONCLUSIVE, never a pass.
    expect(
      screen.getByTestId("reincarnation-falsification").textContent,
    ).toMatch(/INCONCLUSIVE/);
    // Participation: final life stopped betting in its last third.
    expect(
      screen.getByTestId("reincarnation-participation").textContent,
    ).toMatch(/SHUTDOWN/);
  });

  it("renders the mode-switch participation call when betting continues", () => {
    const fx = buildFixture({
      incarnations: [
        inc(1),
        {
          ...inc(2, { died: false, progress_pct: 100, pnl_at_death: 188 }),
          bets_by_third: [
            { third: 0, placed: 5, denominator: 40 },
            { third: 1, placed: 3, denominator: 40 },
            { third: 2, placed: 4, denominator: 40 },
          ],
        },
      ],
      surviving_incarnation: 2,
    });
    render(<ReincarnationShell numerical={fx} ai={null} />);
    expect(
      screen.getByTestId("reincarnation-participation").textContent,
    ).toMatch(/kept betting/);
  });
});
