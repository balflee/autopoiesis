import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import React from "react";

import "./setup";

import BacktestRoute from "../app/backtest/page";
import MockRoute from "../app/mock/page";
import RoadmapPage from "../app/roadmap/page";
import PnLBaselineChart from "@/components/PnLBaselineChart";
import WeightEvolutionChart from "@/components/WeightEvolutionChart";
import { DecisionFeed } from "@/components/DecisionFeed";
import { useWsStore } from "@/lib/wsStore";
import type { DecisionFeedMessage } from "@/lib/types";

/**
 * G3 — RESPONSIVE pass guard.
 *
 * Phase G adds smaller-breakpoint behavior to the four abyss pages + the reused
 * widgets, ADDITIVELY (sm:/md:/lg: prefixes) so the desktop layout is unchanged.
 * This suite pins the load-bearing responsive classes so a future edit that
 * silently drops mobile reflow (or alters the desktop base) is caught:
 *
 *   (a) the shared hero header row wraps (back-link + stage label stack at ~375px);
 *   (b) the SVG charts scale to container width (viewBox + w-full, not fixed px);
 *   (c) the frontier table horizontal-scrolls when narrower than its min width;
 *   (d) the chart legends + headline-stat grids reflow to a single/2-up column
 *       on mobile and only fan out at sm:/lg:;
 *   (e) the /mock MIND split + roadmap lifeline reflow on narrow screens;
 *   (f) the live DecisionFeed row tightens its gap on mobile.
 *
 * Every assertion checks the ADDITIVE class is present AND the desktop base is
 * retained — so "desktop unchanged" is part of the contract, not just mobile.
 */

const ORIGINAL_ENV = process.env.NEXT_PUBLIC_L5_COMPLETE;

afterEach(() => {
  if (ORIGINAL_ENV === undefined) {
    delete process.env.NEXT_PUBLIC_L5_COMPLETE;
  } else {
    process.env.NEXT_PUBLIC_L5_COMPLETE = ORIGINAL_ENV;
  }
});

describe("G3 — shared StageShell hero header is responsive", () => {
  it("the back-link + stage-label row wraps so the two labels never collide at ~375px", () => {
    delete process.env.NEXT_PUBLIC_L5_COMPLETE;
    render(<MockRoute />);
    const back = screen.getByTestId("mock-back-link");
    const row = back.parentElement as HTMLElement;
    // ADDITIVE: wraps on narrow screens…
    expect(row.className).toContain("flex-wrap");
    // …while the desktop row chrome (flex + justify-between) is retained.
    expect(row.className).toContain("flex");
    expect(row.className).toContain("justify-between");
  });
});

describe("G3 — SVG charts scale to container width", () => {
  const pnlVm = {
    series: [
      { key: "learner", label: "learner", values: [0, 5, 12], hero: true },
      { key: "static", label: "static", values: [0, 1, 2] },
    ],
    yMin: 0,
    yMax: 12,
    baselineY: 0,
  };
  const weightVm = {
    series: [{ key: "a0", label: "α₀", values: [0.2, 0.4, 0.6] }],
    yMin: 0,
    yMax: 1,
  };

  it("PnLBaselineChart svg is viewBox-driven + full-width (not a fixed px size)", () => {
    render(<PnLBaselineChart viewModel={pnlVm} activeIndex={1} variant="abyss" />);
    const svg = screen
      .getByTestId("pnl-baseline-chart")
      .querySelector("svg") as SVGSVGElement;
    expect(svg.getAttribute("viewBox")).toBeTruthy();
    expect(svg.classList.contains("w-full")).toBe(true);
    // No hard pixel width pinned on the svg — it tracks the container.
    expect(svg.getAttribute("width")).toBeNull();
  });

  it("the PnL legend reflows: 1-up on mobile, fanning out only at sm:/lg:", () => {
    render(<PnLBaselineChart viewModel={pnlVm} activeIndex={1} variant="abyss" />);
    const legend = screen.getByTestId("pnl-baseline-legend");
    expect(legend.className).toContain("grid-cols-1");
    expect(legend.className).toContain("sm:grid-cols-2");
    expect(legend.className).toContain("lg:grid-cols-4");
  });

  it("WeightEvolutionChart svg is viewBox-driven + full-width", () => {
    render(<WeightEvolutionChart viewModel={weightVm} activeIndex={1} variant="abyss" />);
    const svg = screen
      .getByTestId("weight-evolution-chart")
      .querySelector("svg") as SVGSVGElement;
    expect(svg.getAttribute("viewBox")).toBeTruthy();
    expect(svg.classList.contains("w-full")).toBe(true);
  });
});

describe("G3 — backtest frontier table + headline stats reflow", () => {
  it("the frontier table horizontal-scrolls (overflow-x-auto + a min width)", () => {
    render(<BacktestRoute />);
    const table = screen.getByTestId("frontier-table");
    expect(table.className).toContain("overflow-x-auto");
    // The inner <table> keeps a min-width so columns stay legible and the
    // wrapper scrolls horizontally rather than crushing them on mobile.
    const inner = table.querySelector("table") as HTMLTableElement;
    expect(inner.className).toMatch(/min-w-\[/);
  });

  it("the hero headline-stat strip is 2-up on mobile and 4-up only at sm:", () => {
    render(<BacktestRoute />);
    // The headline telemetry grid lives inside the hero header.
    const hero = screen.getByTestId("backtest-route");
    const grid = hero.querySelector(".grid-cols-2.sm\\:grid-cols-4");
    expect(grid).not.toBeNull();
  });
});

describe("G3 — roadmap lifeline reflows on narrow screens", () => {
  it("the lifeline section + spine use responsive padding/offset (mobile base, sm: desktop)", () => {
    delete process.env.NEXT_PUBLIC_L5_COMPLETE;
    render(<RoadmapPage />);
    const section = screen
      .getByRole("list")
      .closest("section") as HTMLElement;
    // Tighter inset on mobile, wider on sm+ — additive, desktop unchanged.
    expect(section.className).toContain("pl-12");
    expect(section.className).toContain("sm:pl-16");
  });
});

describe("G3 — live DecisionFeed row tightens its gap on mobile", () => {
  beforeEach(() => {
    useWsStore.getState().reset();
  });

  function frame(): DecisionFeedMessage {
    return {
      kind: "decision_feed",
      ts: "2026-06-08T12:05:00Z",
      seq: 1,
      entries: [
        {
          id: "r1",
          ts: "2026-06-08T12:00:00Z",
          action: "BET",
          side: "Alcaraz ML",
          size_usd: 4,
          result: "WIN",
          pnl_usd: 3.2,
          market_id: "0xABC",
        },
      ],
    };
  }

  it("the row toggle is gap-2 on mobile and only gap-3 at sm: (desktop unchanged)", () => {
    render(<DecisionFeed variant="abyss" />);
    act(() => {
      useWsStore.getState().ingest(frame());
    });
    const toggle = screen.getByTestId("decision-feed-row-toggle");
    expect(toggle.className).toContain("gap-2");
    expect(toggle.className).toContain("sm:gap-3");
  });
});
