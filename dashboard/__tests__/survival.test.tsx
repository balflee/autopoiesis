import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import React from "react";

import "./setup";

import SurvivalRoute from "../app/survival/page";
import SurvivalJourneyView from "../app/survival/SurvivalJourneyView";
import {
  SURVIVAL_WEIGHT_KEYS,
  validateSurvivalJourney,
  type SurvivalJourneyFixture,
} from "@/lib/load_survival_journey";

/**
 * Phase D/E smoke test — the /survival interactive view (E1 consumer).
 *
 * The async server page (`app/survival/page.tsx`) reads the gitignored 4 MB
 * artifact from disk, so it is exercised by the loader's real-artifact test;
 * here we render the CLIENT body (`SurvivalJourneyView`) against a small,
 * validated fixture and assert it wires the survival ADAPTER to the three
 * generic chart primitives (P&L / weights / scrubber) without crashing.
 *
 * Mirrors the roadmap/backtest smoke pattern: RTL + `import "./setup"`.
 */

/**
 * jsdom normalises an inline `style.backgroundColor` hex to `rgb(r, g, b)`,
 * while a raw SVG `stroke`/`fill` attribute stays the literal hex. To compare
 * the legend swatch (inline style) against its line (attribute) we lift the hex
 * into the same `rgb()` space. Supports #rgb and #rrggbb.
 */
function hexToRgb(hex: string): string {
  let h = hex.replace("#", "");
  if (h.length === 3) {
    h = h
      .split("")
      .map((c) => c + c)
      .join("");
  }
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return `rgb(${r}, ${g}, ${b})`;
}

function weights(): Record<string, number> {
  return {
    w_r: 0.5,
    w_s: 0.5,
    alpha_0: 0.4,
    alpha_1: 0.35,
    alpha_2: 0.25,
    beta_0: 0.45,
    beta_1: 0.55,
    rho: 0.2,
  };
}
function signals(): Record<string, number> {
  return {
    tennis_technical: 0.6,
    market_momentum: 0.1,
    smart_money: -0.2,
    sentiment_llm: 1,
    crowd_volume: 0,
  };
}
function market(id: string): Record<string, unknown> {
  return {
    entry_price: 0.6,
    market_id: id,
    outcome: "yes",
    players: ["alpha", "bravo"],
    slug: `wta-${id}`,
    surface: "Hard",
  };
}

function buildFixture(): SurvivalJourneyFixture {
  return validateSurvivalJourney({
    seed: {
      max_breath_risk_pct: 0.9,
      min_bet_size_usd: 4,
      min_confidence: 0.05,
      weights: weights(),
    },
    summary: {
      best_life: 1,
      deaths: 1,
      learner_final_pnl: 30,
      learning_vs_static_delta: 25,
      lives: 2,
      static_final_pnl: 5,
      total_steps: 3,
    },
    lives: [
      {
        idx: 0,
        bets: 2,
        pnl: -3,
        death: {
          breath: 0,
          cause: "breath_depleted",
          last_tick: 9,
          kill_tx_hash: "0x0",
          tombstone_token_id: "0",
        },
        final_bankroll_usd: 97,
        final_breath: 0,
        settlements: 2,
        start_ts: "2024-08-30T05:30:02+00:00",
      },
      {
        idx: 1,
        bets: 1,
        pnl: 33,
        death: null,
        final_bankroll_usd: 133,
        final_breath: 900,
        settlements: 1,
        start_ts: "2024-09-01T00:00:00+00:00",
      },
    ],
    steps: [
      {
        life_idx: 0,
        market: market("m1"),
        side: "YES",
        size: 4,
        pnl: 2,
        cum_pnl: 2,
        breath: 30,
        win_rate: 1,
        weights: weights(),
        weights_before: weights(),
        signals: signals(),
      },
      {
        life_idx: 0,
        market: market("m2"),
        side: "NO",
        size: 5,
        pnl: -5,
        cum_pnl: -3,
        breath: 5,
        win_rate: 0.5,
        weights: weights(),
        weights_before: weights(),
        signals: signals(),
      },
      {
        life_idx: 1,
        market: market("m3"),
        side: "YES",
        size: 5,
        pnl: 33,
        cum_pnl: 30,
        breath: 900,
        win_rate: 0.67,
        weights: weights(),
        weights_before: weights(),
        signals: signals(),
      },
    ],
    baselines: {
      static: [
        { idx: 0, cum_pnl: 0, pnl: 0, is_bet: false, market_id: "m1", side: null, size: 0 },
        { idx: 1, cum_pnl: 5, pnl: 5, is_bet: true, market_id: "m2", side: "YES", size: 5 },
      ],
      random: [
        { idx: 0, cum_pnl: -5, pnl: -5, is_bet: true, market_id: "m1", side: "NO", size: 5 },
        { idx: 1, cum_pnl: -5, pnl: 0, is_bet: false, market_id: "m2", side: null, size: 0 },
      ],
      always_favorite: [
        { idx: 0, cum_pnl: 4, pnl: 4, is_bet: true, market_id: "m1", side: "YES", size: 5 },
        { idx: 1, cum_pnl: 9, pnl: 5, is_bet: true, market_id: "m2", side: "YES", size: 5 },
      ],
    },
  });
}

describe("SurvivalJourneyView — adapter → chart primitives smoke", () => {
  it("renders the three chart sections", () => {
    render(<SurvivalJourneyView fixture={buildFixture()} />);
    expect(screen.getByTestId("survival-journey-view")).toBeInTheDocument();
    expect(screen.getByTestId("pnl-baseline-chart")).toBeInTheDocument();
    expect(screen.getByTestId("weight-evolution-chart")).toBeInTheDocument();
    expect(screen.getByTestId("backtest-scrubber")).toBeInTheDocument();
  });

  it("draws a weight line for every fusion-weight key", () => {
    render(<SurvivalJourneyView fixture={buildFixture()} />);
    const chart = screen.getByTestId("weight-evolution-chart");
    for (const k of SURVIVAL_WEIGHT_KEYS) {
      expect(within(chart).getByTestId(`weight-line-${k}`)).toBeInTheDocument();
    }
  });

  it("draws the learner hero P&L line + the three baseline lines", () => {
    render(<SurvivalJourneyView fixture={buildFixture()} />);
    const chart = screen.getByTestId("pnl-baseline-chart");
    expect(within(chart).getByTestId("pnl-line-learner")).toBeInTheDocument();
    expect(within(chart).getByTestId("pnl-line-static")).toBeInTheDocument();
    expect(within(chart).getByTestId("pnl-line-random")).toBeInTheDocument();
    expect(within(chart).getByTestId("pnl-line-always_favorite")).toBeInTheDocument();
  });

  it("keeps each P&L series' line, dot, and legend swatch the SAME color", () => {
    // Regression guard (codex M5): the three render sites once used three
    // different index bases, so the legend swatch landed +1 off its line
    // (static line=amber but legend swatch=sky, etc.). Assert per-series that
    // the SVG line stroke === the current-sample dot fill === the legend
    // swatch background, so the line<->name legend stays legible.
    render(<SurvivalJourneyView fixture={buildFixture()} />);
    const chart = screen.getByTestId("pnl-baseline-chart");
    for (const key of ["learner", "static", "random", "always_favorite"]) {
      const line = within(chart).getByTestId(`pnl-line-${key}`);
      const dot = within(chart).getByTestId(`pnl-current-dot-${key}`);
      const legend = within(chart).getByTestId(`pnl-legend-${key}`);
      const swatch = legend.querySelector("span[aria-hidden]") as HTMLElement | null;

      const lineColor = line.getAttribute("stroke");
      const dotColor = dot.getAttribute("fill");
      const swatchColor = swatch?.style.backgroundColor ?? null;

      expect(lineColor, `${key} line stroke`).toBeTruthy();
      expect(dotColor, `${key} dot fill`).toBe(lineColor);
      // The legend swatch must visually match its line (browsers normalise the
      // inline hex to rgb(), so compare on the resolved value).
      expect(swatchColor, `${key} legend swatch`).toBe(hexToRgb(lineColor!));
    }
  });

  it("renders the survival P&L chart in the ABYSS palette (lime learner, no navy/amber)", () => {
    // /survival passes `variant="abyss"` so the chart is cohesive with the
    // abyssal design system: the learner (hero) line glows electric-lime
    // (--ab-glow #c8f94c) and NONE of the chart paints the legacy navy
    // (#0B1426) or amber (#FFB703) — the cold Phase-1 colors that broke the
    // page's visual continuity.
    render(<SurvivalJourneyView fixture={buildFixture()} />);
    const chart = screen.getByTestId("pnl-baseline-chart");

    // Hero learner line is the bioluminescent lime accent.
    expect(
      within(chart).getByTestId("pnl-line-learner").getAttribute("stroke")?.toLowerCase(),
    ).toBe("#c8f94c");
    // The scrubber indicator is lime, not amber.
    expect(
      within(chart).getByTestId("pnl-baseline-indicator").getAttribute("stroke")?.toLowerCase(),
    ).toBe("#c8f94c");

    // No legacy navy/amber anywhere in the chart's SVG attributes.
    const svg = chart.querySelector("svg")!;
    const markup = svg.outerHTML.toLowerCase();
    expect(markup).not.toContain("#0b1426"); // navy bg
    expect(markup).not.toContain("#ffb703"); // amber
    expect(markup).not.toContain("#06d6a0"); // win-teal (legacy hero)
  });

  it("renders the survival weight + scrubber charts in the ABYSS palette", () => {
    render(<SurvivalJourneyView fixture={buildFixture()} />);

    // Weight chart: dominant (first-rendered) weight line glows lime; the
    // scrubber indicator is lime; no amber.
    const weight = screen.getByTestId("weight-evolution-chart");
    const firstKey = SURVIVAL_WEIGHT_KEYS[0];
    expect(
      within(weight).getByTestId(`weight-line-${firstKey}`).getAttribute("stroke")?.toLowerCase(),
    ).toBe("#c8f94c");
    expect(
      within(weight).getByTestId("weight-evolution-indicator").getAttribute("stroke")?.toLowerCase(),
    ).toBe("#c8f94c");
    expect(weight.querySelector("svg")!.outerHTML.toLowerCase()).not.toContain("#ffb703");

    // Scrubber: the <style jsx> track/thumb must be lime, never the legacy
    // amber thumb or navy thumb-border.
    const scrubber = screen.getByTestId("backtest-scrubber");
    const styleText = (scrubber.querySelector("style")?.textContent ?? "").toLowerCase();
    expect(styleText).toContain("#c8f94c"); // lime fill/thumb
    expect(styleText).not.toContain("#ffb703"); // no amber thumb
    expect(styleText).not.toContain("#0b1426"); // no navy thumb-border
    // Death marker stays in the permadeath color (a CSS var, untouched).
    expect(
      within(scrubber).getByTestId("scrubber-death-0").style.backgroundColor,
    ).toContain("--ab-death");
  });

  it("marks the one death on the scrubber", () => {
    render(<SurvivalJourneyView fixture={buildFixture()} />);
    const scrubber = screen.getByTestId("backtest-scrubber");
    expect(within(scrubber).getByTestId("scrubber-death-0")).toBeInTheDocument();
    // The scrubber spans 3 steps (0..2).
    expect(
      within(scrubber).getByTestId("backtest-scrubber-range").getAttribute("aria-valuemax"),
    ).toBe("2");
  });

  it("draws the life-boundary marks on the scrubber (adapter→component wiring)", () => {
    // Regression guard: the adapter must emit boundary fractions under the key
    // the BacktestScrubber actually reads (`viewModel.boundaries`). Two distinct
    // lives (life_idx 0, 0, 1) → two boundary marks.
    render(<SurvivalJourneyView fixture={buildFixture()} />);
    const scrubber = screen.getByTestId("backtest-scrubber");
    expect(within(scrubber).getByTestId("scrubber-boundary-0")).toBeInTheDocument();
    expect(within(scrubber).getByTestId("scrubber-boundary-1")).toBeInTheDocument();
  });

  /* ── E2 STAR widgets ─────────────────────────────────────────────── */

  it("renders the E2 star widgets: vitals, current-bet card, graveyard", () => {
    render(<SurvivalJourneyView fixture={buildFixture()} />);
    const view = screen.getByTestId("survival-journey-view");
    expect(within(view).getByTestId("survival-vitals")).toBeInTheDocument();
    expect(within(view).getByTestId("survival-match-card")).toBeInTheDocument();
    expect(within(view).getByTestId("tombstone-strip")).toBeInTheDocument();
  });

  it("surfaces the headline learner-vs-static delta", () => {
    render(<SurvivalJourneyView fixture={buildFixture()} />);
    const delta = screen.getByTestId("survival-headline-delta");
    // learner 30, static 5 → +25 apart (money() rounds to whole dollars).
    expect(delta.textContent).toContain("$30");
    expect(delta.textContent).toContain("$5");
    expect(delta.textContent).toContain("$25");
  });

  it("reads the current bet straight from the journey step (players/side/signals)", () => {
    render(<SurvivalJourneyView fixture={buildFixture()} />);
    const card = screen.getByTestId("survival-match-card");
    // step 0: alpha vs bravo, YES, Hard surface.
    expect(within(card).getByTestId("survival-match-player-a").textContent).toBe("Alpha");
    expect(within(card).getByTestId("survival-match-player-b").textContent).toBe("Bravo");
    expect(within(card).getByTestId("survival-match-side").textContent).toBe("YES");
    expect(within(card).getByTestId("survival-match-surface").textContent).toBe("Hard");
    // all five engine signals are drawn.
    for (const k of [
      "tennis_technical",
      "market_momentum",
      "smart_money",
      "sentiment_llm",
      "crowd_volume",
    ]) {
      expect(within(card).getByTestId(`survival-signal-${k}`)).toBeInTheDocument();
    }
  });

  it("surfaces an AI `reflection` annotation on the match card when present", () => {
    // Stamp a reflection on step 0 (the auto-play start frame) — the AI-run
    // annotation should surface as a one-line callout on the current-bet card.
    const raw = JSON.parse(
      JSON.stringify(buildFixture()),
    ) as unknown as Record<string, unknown>;
    const note = "reflected (tick_interval) #1 -> proposed 1 proposal (pending approval)";
    (((raw.steps as Record<string, unknown>[])[0]) as Record<string, unknown>).reflection = note;
    const aiFixture = validateSurvivalJourney(raw);

    render(<SurvivalJourneyView fixture={aiFixture} />);
    const callout = screen.getByTestId("survival-match-reflection");
    expect(callout).toBeInTheDocument();
    expect(callout.textContent).toContain(note);
  });

  it("omits the reflection callout on a numerical run (no annotation)", () => {
    render(<SurvivalJourneyView fixture={buildFixture()} />);
    expect(screen.queryByTestId("survival-match-reflection")).not.toBeInTheDocument();
  });

  it("renders one tombstone per dead life, none for survivors", () => {
    render(<SurvivalJourneyView fixture={buildFixture()} />);
    const strip = screen.getByTestId("tombstone-strip");
    // life 0 died, life 1 survived → exactly one tombstone (life 0).
    expect(within(strip).getByTestId("tombstone-0")).toBeInTheDocument();
    expect(within(strip).queryByTestId("tombstone-1")).not.toBeInTheDocument();
  });

  it("shows the breath vitals gauge with a status", () => {
    render(<SurvivalJourneyView fixture={buildFixture()} />);
    const vitals = screen.getByTestId("survival-vitals");
    expect(within(vitals).getByTestId("survival-breath-bar")).toBeInTheDocument();
    expect(within(vitals).getByTestId("survival-vitals-status")).toBeInTheDocument();
    expect(within(vitals).getByTestId("survival-vitals-waveform")).toBeInTheDocument();
  });
});

/**
 * FIX 3 — the forward "next · live mock-bet" lifeline link is GATED on
 * readL5Complete() (NEXT_PUBLIC_L5_COMPLETE). /mock is sealed until L5 finishes,
 * so the survival footer must not offer a link into a locked stage. The back
 * link (◂ back to the seed → /backtest) is always present.
 *
 * The route is an async server component reading the real survival_journey.json
 * artifact from disk (present in this checkout), so we await it then render the
 * resolved element — mirroring the env-flip pattern in roadmap.test.tsx.
 */
describe("SurvivalRoute — forward next-link L5 gate (F2 fix)", () => {
  const ORIGINAL_ENV = process.env.NEXT_PUBLIC_L5_COMPLETE;

  afterEach(() => {
    if (ORIGINAL_ENV === undefined) {
      delete process.env.NEXT_PUBLIC_L5_COMPLETE;
    } else {
      process.env.NEXT_PUBLIC_L5_COMPLETE = ORIGINAL_ENV;
    }
  });

  it("when L5 is NOT complete: the next → /mock link is ABSENT (back link stays)", async () => {
    delete process.env.NEXT_PUBLIC_L5_COMPLETE;
    render(await SurvivalRoute());

    expect(screen.queryByTestId("survival-next-link")).toBeNull();
    // The back-to-roadmap lifeline link is always present.
    expect(screen.getByTestId("survival-back-link")).toBeInTheDocument();
  });

  it("when NEXT_PUBLIC_L5_COMPLETE is set: the next → /mock link renders", async () => {
    process.env.NEXT_PUBLIC_L5_COMPLETE = "true";
    render(await SurvivalRoute());

    const next = screen.getByTestId("survival-next-link");
    expect(next).toBeInTheDocument();
    expect(next.getAttribute("href")).toBe("/mock");
  });
});

/**
 * G1 — the shared lifeline StageShell renders the survival stage's chrome: the
 * APPRENTICE stage label, the ◂ lifeline back-link to /roadmap, and the footer's
 * backward cross-link to the previous lifeline stage (◂ back to the seed →
 * /backtest), which is always present regardless of the L5 gate.
 */
describe("SurvivalRoute — shared StageShell nav (G1)", () => {
  const ORIGINAL_ENV = process.env.NEXT_PUBLIC_L5_COMPLETE;

  afterEach(() => {
    if (ORIGINAL_ENV === undefined) {
      delete process.env.NEXT_PUBLIC_L5_COMPLETE;
    } else {
      process.env.NEXT_PUBLIC_L5_COMPLETE = ORIGINAL_ENV;
    }
  });

  it("renders the apprentice stage label + ◂ lifeline back-link to /roadmap", async () => {
    delete process.env.NEXT_PUBLIC_L5_COMPLETE;
    render(await SurvivalRoute());

    expect(screen.getByText("apprentice · learning to survive")).toBeInTheDocument();
    const back = screen.getByTestId("survival-back-link");
    expect(back).toHaveTextContent("◂ lifeline");
    expect(back.getAttribute("href")).toBe("/roadmap");
  });

  it("backward-links to the previous lifeline stage (/backtest)", async () => {
    delete process.env.NEXT_PUBLIC_L5_COMPLETE;
    render(await SurvivalRoute());

    const prev = screen
      .getByText(/back to the seed/i)
      .closest("a") as HTMLAnchorElement | null;
    expect(prev).not.toBeNull();
    expect(prev?.getAttribute("href")).toBe("/backtest");
  });
});
