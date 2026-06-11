import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import React from "react";

import "../setup";

import PnLBaselineChart, {
  type PnlBaselineViewModel,
} from "../../components/PnLBaselineChart";
import WeightEvolutionChart, {
  type WeightEvolutionViewModel,
} from "../../components/WeightEvolutionChart";
import BacktestScrubber, {
  type ScrubberViewModel,
} from "../../components/BacktestScrubber";
import { AbyssColors, ColorTokens } from "../../lib/colorTokens";

/**
 * Theme-variant guard for the three SHARED chart primitives.
 *
 * /survival recolors these charts to the abyssal palette via an ADDITIVE
 * `variant` prop. This suite pins both halves of that contract:
 *
 *   1. `variant` defaults to `"navy"` → the LEGACY Phase-1 colors are emitted
 *      unchanged (so /backtest, /playback, /workshop callers — present or
 *      future — keep their navy look byte-for-byte).
 *   2. `variant="abyss"` → the chart uses the bioluminescent lime/moss palette
 *      and emits NONE of the legacy navy (#0B1426) / amber (#FFB703).
 */

const lower = (s: string | null | undefined): string => (s ?? "").toLowerCase();

function pnlVm(): PnlBaselineViewModel {
  return {
    series: [
      { key: "agent", label: "Agent", values: [0, 5, 12], hero: true },
      { key: "base", label: "Baseline", values: [0, 1, 2] },
    ],
    yMin: -5,
    yMax: 15,
    baselineY: 0,
  };
}

function weightVm(): WeightEvolutionViewModel {
  return {
    series: [
      { key: "a0", label: "α₀", values: [0.2, 0.4, 0.6] },
      { key: "rho", label: "ρ", values: [0.1, 0.2, 0.3] },
    ],
  };
}

function scrubberVm(): ScrubberViewModel {
  return { stepCount: 3, boundaries: [0, 0.5], deaths: [{ stepIndex: 1 }] };
}

describe("Chart primitives — variant theming (additive prop)", () => {
  describe("default variant is navy (legacy callers byte-unchanged)", () => {
    it("PnLBaselineChart hero line + indicator use the legacy navy palette", () => {
      render(<PnLBaselineChart viewModel={pnlVm()} activeIndex={1} />);
      const chart = screen.getByTestId("pnl-baseline-chart");
      // Hero is WIN-teal, indicator is AMBER — the original Phase-1 colors.
      expect(lower(within(chart).getByTestId("pnl-line-agent").getAttribute("stroke"))).toBe(
        lower(ColorTokens.WIN),
      );
      expect(
        lower(within(chart).getByTestId("pnl-baseline-indicator").getAttribute("stroke")),
      ).toBe(lower(ColorTokens.AMBER));
    });

    it("WeightEvolutionChart first line is WIN-teal, indicator AMBER", () => {
      render(<WeightEvolutionChart viewModel={weightVm()} activeIndex={1} />);
      const chart = screen.getByTestId("weight-evolution-chart");
      expect(lower(within(chart).getByTestId("weight-line-a0").getAttribute("stroke"))).toBe(
        lower(ColorTokens.WIN),
      );
      expect(
        lower(within(chart).getByTestId("weight-evolution-indicator").getAttribute("stroke")),
      ).toBe(lower(ColorTokens.AMBER));
    });

    it("BacktestScrubber thumb/track keep the legacy navy-border amber thumb", () => {
      render(
        <BacktestScrubber viewModel={scrubberVm()} activeIndex={1} onChange={() => {}} />,
      );
      const scrubber = screen.getByTestId("backtest-scrubber");
      const style = lower(scrubber.querySelector("style")?.textContent);
      expect(style).toContain(lower(ColorTokens.WIN)); // track fill
      expect(style).toContain(lower(ColorTokens.AMBER)); // thumb
      expect(style).toContain(lower(ColorTokens.BG)); // navy thumb border
    });
  });

  describe('variant="abyss" recolors to the bioluminescent palette', () => {
    it("PnLBaselineChart: lime hero + indicator, no navy/amber", () => {
      render(<PnLBaselineChart viewModel={pnlVm()} activeIndex={1} variant="abyss" />);
      const chart = screen.getByTestId("pnl-baseline-chart");
      expect(lower(within(chart).getByTestId("pnl-line-agent").getAttribute("stroke"))).toBe(
        lower(AbyssColors.GLOW),
      );
      expect(
        lower(within(chart).getByTestId("pnl-baseline-indicator").getAttribute("stroke")),
      ).toBe(lower(AbyssColors.GLOW));
      const markup = lower(chart.querySelector("svg")?.outerHTML);
      expect(markup).not.toContain(lower(ColorTokens.BG)); // no navy
      expect(markup).not.toContain(lower(ColorTokens.AMBER)); // no amber
    });

    it("WeightEvolutionChart: lime dominant line + indicator, no amber", () => {
      render(<WeightEvolutionChart viewModel={weightVm()} activeIndex={1} variant="abyss" />);
      const chart = screen.getByTestId("weight-evolution-chart");
      expect(lower(within(chart).getByTestId("weight-line-a0").getAttribute("stroke"))).toBe(
        lower(AbyssColors.GLOW),
      );
      expect(
        lower(within(chart).getByTestId("weight-evolution-indicator").getAttribute("stroke")),
      ).toBe(lower(AbyssColors.GLOW));
      expect(lower(chart.querySelector("svg")?.outerHTML)).not.toContain(lower(ColorTokens.AMBER));
    });

    it("BacktestScrubber: lime track/thumb, no navy border or amber thumb", () => {
      render(
        <BacktestScrubber
          viewModel={scrubberVm()}
          activeIndex={1}
          onChange={() => {}}
          variant="abyss"
        />,
      );
      const scrubber = screen.getByTestId("backtest-scrubber");
      const style = lower(scrubber.querySelector("style")?.textContent);
      expect(style).toContain(lower(AbyssColors.GLOW)); // lime fill + thumb
      expect(style).not.toContain(lower(ColorTokens.AMBER)); // no amber thumb
      expect(style).not.toContain(lower(ColorTokens.BG)); // no navy border
    });
  });
});
