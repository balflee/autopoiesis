"use client";

/**
 * WeightEvolutionChart — reusable weight-learning-curves primitive.
 *
 * Originally Phase-1-fixture-bound (T-D-008); refactored (E1 / codex M5) to
 * accept a GENERIC, fixture-agnostic view-model so it can render BOTH the
 * Phase-1 training journey AND the survival run without either fixture being
 * cast into the other. The caller supplies an ordered list of named series
 * (each a per-x array of weight values) + a y-domain; the chart draws one SVG
 * path per series, a scrubber indicator at `activeIndex`, and a legend with the
 * current value per series.
 *
 * Survival adapter: `adaptWeightViewModel` in `lib/load_survival_journey.ts`
 * produces this exact view-model from a `SurvivalJourneyFixture`.
 *
 * Implementation: bespoke SVG paths computed ONCE via `useMemo` over the whole
 * series set — the `activeIndex` only moves a single vertical indicator line,
 * so scrubbing is O(1) per frame.
 */

import { useMemo, type JSX } from "react";

import { AbyssColors, ColorTokens, type ChartVariant } from "@/lib/colorTokens";

const VIEWBOX_W = 720;
const VIEWBOX_H = 280;
const PADDING = { top: 18, right: 18, bottom: 32, left: 40 };
const INNER_W = VIEWBOX_W - PADDING.left - PADDING.right;
const INNER_H = VIEWBOX_H - PADDING.top - PADDING.bottom;

/** Per-variant weight-line + axis palette. */
interface WeightPalette {
  /** Cycled across weight series in render order. */
  readonly series: readonly string[];
  /** Axes, gridlines, tick labels. */
  readonly axis: string;
  /** The vertical scrubber indicator. */
  readonly indicator: string;
}

/**
 * The original Phase-1 series palette (navy). Preserved byte-for-byte so the
 * default render is unchanged.
 */
const NAVY_SERIES_PALETTE: readonly string[] = [
  ColorTokens.WIN,
  ColorTokens.AMBER,
  ColorTokens.LOSS,
  "#7DD3FC", // sky-300
  ColorTokens.INK,
  "#c8f94c", // electric lime (abyss glow)
  ColorTokens.INK_MUTED,
  "#f0abfc", // fuchsia-300
];

/**
 * Abyss series palette: the FIRST line lands on the bioluminescent lime so the
 * dominant weight (the survival adapter emits α₀ / the most-moved weight at the
 * front of the render order) glows; the rest are legible moss / teal / dim
 * tones with a single warm death-red, all readable on the near-black floor.
 */
const ABYSS_SERIES_PALETTE: readonly string[] = [
  AbyssColors.GLOW, // dominant weight — electric lime
  "#7fd0b6", // bright moss-teal
  AbyssColors.MOSS, // moss
  "#7DD3FC", // sky (kept — reads well on dark)
  AbyssColors.TEXT, // bright body text line
  AbyssColors.DEATH, // warm contrast line
  AbyssColors.DIM, // dim secondary
  "#b6f06b", // pale lime
];

const WEIGHT_PALETTES: Record<ChartVariant, WeightPalette> = {
  navy: {
    series: NAVY_SERIES_PALETTE,
    axis: ColorTokens.INK_MUTED,
    indicator: ColorTokens.AMBER,
  },
  abyss: {
    series: ABYSS_SERIES_PALETTE,
    axis: AbyssColors.DIM,
    indicator: AbyssColors.GLOW,
  },
};

/** One named weight series — a per-x array of values in the chart y-domain. */
export interface WeightChartSeries {
  readonly key: string;
  readonly label: string;
  readonly values: readonly number[];
  /** Optional explicit color; defaults to the cycled palette by index. */
  readonly color?: string;
}

/** Generic, fixture-agnostic view-model the chart renders. */
export interface WeightEvolutionViewModel {
  readonly series: readonly WeightChartSeries[];
  /** y-domain. Defaults to [0,1] when omitted. */
  readonly yMin?: number;
  readonly yMax?: number;
  /** Optional sub-title shown in the header (e.g. "842 bets · 7 lives"). */
  readonly subtitle?: string;
  /** Optional footnote (e.g. a Phase-1 invariant note). */
  readonly note?: string;
}

interface XY {
  readonly x: number;
  readonly y: number;
}

function project(
  values: readonly number[],
  yMin: number,
  yMax: number,
): readonly XY[] {
  const n = values.length;
  if (n === 0) return [];
  const xMax = Math.max(n - 1, 1);
  const yRange = Math.max(yMax - yMin, 1e-9);
  return values.map((v, i) => {
    const xPct = i / xMax;
    const yPct = Math.max(0, Math.min(1, (v - yMin) / yRange));
    return {
      x: PADDING.left + xPct * INNER_W,
      y: PADDING.top + (1 - yPct) * INNER_H,
    };
  });
}

function pathFromPoints(pts: readonly XY[]): string {
  if (pts.length === 0) return "";
  return pts
    .map((p, i) =>
      i === 0
        ? `M ${p.x.toFixed(2)} ${p.y.toFixed(2)}`
        : `L ${p.x.toFixed(2)} ${p.y.toFixed(2)}`,
    )
    .join(" ");
}

export interface WeightEvolutionChartProps {
  readonly viewModel: WeightEvolutionViewModel;
  /** 0-based active x sample (the scrubber position). */
  readonly activeIndex: number;
  /**
   * Color theme. Defaults to `"navy"` (the original Phase-1 palette — legacy
   * callers are byte-unchanged). The /survival page passes `"abyss"`.
   */
  readonly variant?: ChartVariant;
}

export function WeightEvolutionChart({
  viewModel,
  activeIndex,
  variant = "navy",
}: WeightEvolutionChartProps): JSX.Element {
  const yMin = viewModel.yMin ?? 0;
  const yMax = viewModel.yMax ?? 1;
  const palette = WEIGHT_PALETTES[variant];

  const { paths, sampleCount, gridLabels } = useMemo(() => {
    const built = viewModel.series.map((s) => pathFromPoints(project(s.values, yMin, yMax)));
    const count = viewModel.series.reduce((m, s) => Math.max(m, s.values.length), 0);
    // Five evenly spaced y gridline labels across the domain.
    const labels = [0, 0.25, 0.5, 0.75, 1].map((p) => yMin + p * (yMax - yMin));
    return { paths: built, sampleCount: count, gridLabels: labels };
  }, [viewModel.series, yMin, yMax]);

  const lastIdx = Math.max(sampleCount - 1, 0);
  const clampedIdx = Math.max(0, Math.min(lastIdx, Math.trunc(activeIndex)));
  const indicatorX = PADDING.left + (clampedIdx / Math.max(lastIdx, 1)) * INNER_W;

  const colorFor = (s: WeightChartSeries, i: number): string =>
    s.color ?? palette.series[i % palette.series.length]!;

  return (
    <section
      data-testid="weight-evolution-chart"
      role="region"
      aria-label="Weight learning curves"
      className="flex w-full flex-col gap-3 rounded-xl border border-[var(--ab-moss)]/30 bg-[var(--ab-bg-2)]/60 p-4 text-[var(--ab-text)] sm:p-5"
    >
      <header className="flex flex-wrap items-baseline justify-between gap-3 font-mono text-[11px] uppercase tracking-[0.22em] text-[var(--ab-dim)]">
        <span>weight evolution</span>
        {viewModel.subtitle ? (
          <span className="text-[var(--ab-text)]">{viewModel.subtitle}</span>
        ) : null}
      </header>

      <div className="w-full overflow-hidden">
        <svg
          viewBox={`0 0 ${VIEWBOX_W} ${VIEWBOX_H}`}
          preserveAspectRatio="xMidYMid meet"
          className="block w-full"
          role="img"
          aria-label="Weight trajectories across the run"
        >
          {/* Gridlines + y labels */}
          {[0, 0.25, 0.5, 0.75, 1].map((p, i) => {
            const y = PADDING.top + (1 - p) * INNER_H;
            return (
              <g key={p}>
                <line
                  x1={PADDING.left}
                  x2={VIEWBOX_W - PADDING.right}
                  y1={y}
                  y2={y}
                  stroke={palette.axis}
                  strokeOpacity={p === 0 || p === 1 ? 0.35 : 0.1}
                  strokeWidth={1}
                />
                <text
                  x={PADDING.left - 6}
                  y={y + 3}
                  fill={palette.axis}
                  fontSize={9}
                  textAnchor="end"
                  fontFamily="ui-monospace, monospace"
                >
                  {gridLabels[i]!.toFixed(2)}
                </text>
              </g>
            );
          })}

          {/* Y-axis vertical */}
          <line
            x1={PADDING.left}
            x2={PADDING.left}
            y1={PADDING.top}
            y2={VIEWBOX_H - PADDING.bottom}
            stroke={palette.axis}
            strokeOpacity={0.4}
            strokeWidth={1}
          />

          {/* Lines */}
          {viewModel.series.map((s, i) => (
            <path
              key={s.key}
              data-testid={`weight-line-${s.key}`}
              d={paths[i]}
              fill="none"
              stroke={colorFor(s, i)}
              strokeWidth={1.75}
              strokeLinecap="round"
              strokeLinejoin="round"
              opacity={0.95}
            />
          ))}

          {/* Scrubber indicator */}
          <line
            data-testid="weight-evolution-indicator"
            x1={indicatorX}
            x2={indicatorX}
            y1={PADDING.top}
            y2={VIEWBOX_H - PADDING.bottom}
            stroke={palette.indicator}
            strokeWidth={1.5}
            strokeDasharray="4 4"
            opacity={0.85}
          />

          {/* X-axis labels */}
          <text
            x={PADDING.left}
            y={VIEWBOX_H - PADDING.bottom + 16}
            fill={palette.axis}
            fontSize={9}
            fontFamily="ui-monospace, monospace"
          >
            step 0
          </text>
          <text
            x={VIEWBOX_W - PADDING.right}
            y={VIEWBOX_H - PADDING.bottom + 16}
            fill={palette.axis}
            fontSize={9}
            textAnchor="end"
            fontFamily="ui-monospace, monospace"
          >
            step {lastIdx}
          </text>
        </svg>
      </div>

      {/* Legend with the current value per series. Greek letters (α/β/ρ) MUST
          keep their lowercase glyph — CSS text-transform:uppercase silently
          maps U+03B1 → U+0391 ("α" → "Α") which renders as Latin "A". We
          override on the weight-name span only. */}
      <ul
        data-testid="weight-evolution-legend"
        className="grid grid-cols-2 gap-x-4 gap-y-1.5 font-mono text-[10px] uppercase tracking-[0.18em] text-[var(--ab-dim)] sm:grid-cols-3 lg:grid-cols-4"
      >
        {viewModel.series.map((s, i) => {
          const current = s.values[clampedIdx] ?? s.values[s.values.length - 1] ?? 0;
          return (
            <li
              key={s.key}
              data-testid={`weight-legend-${s.key}`}
              className="flex items-center gap-2"
            >
              <span
                aria-hidden
                className="inline-block h-[2px] w-5 flex-none rounded-full"
                style={{ backgroundColor: colorFor(s, i), opacity: 0.95 }}
              />
              <span className="text-[var(--ab-text)] normal-case">{s.label}</span>
              <span data-testid={`weight-current-${s.key}`}>{current.toFixed(3)}</span>
            </li>
          );
        })}
      </ul>

      {viewModel.note ? (
        <p
          data-testid="weight-evolution-note"
          className="font-mono text-[10px] uppercase tracking-[0.18em] text-[var(--ab-glow)]"
        >
          {viewModel.note}
        </p>
      ) : null}
    </section>
  );
}

export default WeightEvolutionChart;
