"use client";

/**
 * PnLBaselineChart — reusable cumulative-P&L-vs-baselines primitive.
 *
 * Originally Phase-1-fixture-bound (T-D-008); refactored (E1 / codex M5) to
 * accept a GENERIC, fixture-agnostic view-model so it can overlay the agent's
 * cumulative P&L against any set of baseline curves — the Phase-1 archetypes OR
 * the survival run's learner-vs-static/random/always-favorite — without casting
 * one fixture into the other.
 *
 * The caller supplies named series (one flagged `hero` = the agent line, drawn
 * last + boldest), a shared y-domain, and a `baselineY` reference (break-even).
 * Survival adapter: `adaptPnlViewModel` in `lib/load_survival_journey.ts`.
 *
 * A vertical indicator + per-series dot mark the active x sample so the audience
 * can read "where we are now" against each curve. O(1) per scrub.
 */

import { useMemo, type JSX } from "react";

import { AbyssColors, ColorTokens, type ChartVariant } from "@/lib/colorTokens";

const VIEWBOX_W = 720;
const VIEWBOX_H = 280;
const PADDING = { top: 22, right: 18, bottom: 32, left: 64 };
const INNER_W = VIEWBOX_W - PADDING.left - PADDING.right;
const INNER_H = VIEWBOX_H - PADDING.top - PADDING.bottom;

/**
 * Per-variant chart palette. The `navy` set reproduces the original Phase-1
 * colors EXACTLY (so the default render is byte-for-byte unchanged); the
 * `abyss` set renders the chart lime-on-near-black to match the /survival page.
 */
interface PnlPalette {
  /** Cycled across non-hero (baseline) series in render order. */
  readonly baseline: readonly string[];
  /** The hero (agent / learner) line. */
  readonly hero: string;
  /** Axes, gridlines, tick labels. */
  readonly axis: string;
  /** The break-even reference line. */
  readonly baselineRef: string;
  /** The vertical scrubber indicator. */
  readonly indicator: string;
  /** Stroke ring around the current-sample dots (so they read on the floor). */
  readonly dotStroke: string;
  /** Positive / negative bankroll readout colors in the legend. */
  readonly win: string;
  readonly loss: string;
}

const PNL_PALETTES: Record<ChartVariant, PnlPalette> = {
  navy: {
    baseline: [ColorTokens.AMBER, "#7DD3FC", ColorTokens.INK_MUTED, ColorTokens.LOSS, "#f0abfc"],
    hero: ColorTokens.WIN,
    axis: ColorTokens.INK_MUTED,
    baselineRef: ColorTokens.INK,
    indicator: ColorTokens.AMBER,
    dotStroke: ColorTokens.BG,
    win: ColorTokens.WIN,
    loss: ColorTokens.LOSS,
  },
  abyss: {
    // The static seed (first baseline) reads as a dim moss line the lime
    // learner tears away from; the rest stay quiet so the hero dominates.
    baseline: [AbyssColors.MOSS, AbyssColors.DIM, "#5a8f7f", AbyssColors.DEATH, "#7fae9e"],
    hero: AbyssColors.GLOW,
    axis: AbyssColors.DIM,
    baselineRef: AbyssColors.DIM,
    indicator: AbyssColors.GLOW,
    dotStroke: AbyssColors.BG,
    win: AbyssColors.GLOW,
    loss: AbyssColors.DEATH,
  },
};

/** One named cumulative-P&L curve. */
export interface PnlChartSeries {
  readonly key: string;
  readonly label: string;
  readonly values: readonly number[];
  /** The agent / hero line — drawn last, boldest, in WIN green. */
  readonly hero?: boolean;
  readonly color?: string;
}

/** Generic, fixture-agnostic P&L view-model. */
export interface PnlBaselineViewModel {
  readonly series: readonly PnlChartSeries[];
  readonly yMin: number;
  readonly yMax: number;
  /** Break-even reference y (typically 0 for cumulative P&L). */
  readonly baselineY: number;
  readonly subtitle?: string;
}

interface XY {
  readonly x: number;
  readonly y: number;
}

function projectY(v: number, yMin: number, yMax: number): number {
  const yPct = (v - yMin) / Math.max(yMax - yMin, 1e-9);
  return PADDING.top + (1 - yPct) * INNER_H;
}

function project(values: readonly number[], yMin: number, yMax: number): readonly XY[] {
  const n = values.length;
  if (n === 0) return [];
  const xMax = Math.max(n - 1, 1);
  return values.map((v, i) => ({
    x: PADDING.left + (i / xMax) * INNER_W,
    y: projectY(v, yMin, yMax),
  }));
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

export interface PnLBaselineChartProps {
  readonly viewModel: PnlBaselineViewModel;
  readonly activeIndex: number;
  /**
   * Color theme. Defaults to `"navy"` (the original Phase-1 palette — legacy
   * callers are byte-unchanged). The /survival page passes `"abyss"` so the
   * chart renders lime-on-near-black, cohesive with the abyssal design system.
   */
  readonly variant?: ChartVariant;
}

export function PnLBaselineChart({
  viewModel,
  activeIndex,
  variant = "navy",
}: PnLBaselineChartProps): JSX.Element {
  const { yMin, yMax, baselineY } = viewModel;
  const palette = PNL_PALETTES[variant];

  // Render order: baselines first, hero last so it sits on top.
  const ordered = useMemo(() => {
    const baselines = viewModel.series.filter((s) => !s.hero);
    const heroes = viewModel.series.filter((s) => s.hero);
    return [...baselines, ...heroes];
  }, [viewModel.series]);

  // One stable key->color map, derived once from the canonical (baseline-first)
  // `ordered` sequence. Every render site (lines, dots, legend) reads from this
  // map so a series' line, its current-sample dot, and its legend swatch are
  // ALWAYS the same color — regardless of the order a given site iterates in.
  // (Previously three sites computed colors with three different index bases,
  // leaving the legend swatch offset +1 from its line — codex M5 review.)
  const colorByKey = useMemo(() => {
    const map = new Map<string, string>();
    let baselineIdx = 0;
    for (const s of ordered) {
      let color: string;
      if (s.color) {
        color = s.color;
      } else if (s.hero) {
        color = palette.hero;
      } else {
        color = palette.baseline[baselineIdx % palette.baseline.length]!;
        baselineIdx += 1;
      }
      map.set(s.key, color);
    }
    return map;
  }, [ordered, palette]);

  const colorFor = (s: PnlChartSeries): string =>
    colorByKey.get(s.key) ?? (s.hero ? palette.hero : palette.baseline[0]!);

  const paths = useMemo(
    () => ordered.map((s) => pathFromPoints(project(s.values, yMin, yMax))),
    [ordered, yMin, yMax],
  );

  const sampleCount = ordered.reduce((m, s) => Math.max(m, s.values.length), 0);
  const lastIdx = Math.max(sampleCount - 1, 0);
  const clampedIdx = Math.max(0, Math.min(lastIdx, Math.trunc(activeIndex)));
  const indicatorX = PADDING.left + (clampedIdx / Math.max(lastIdx, 1)) * INNER_W;

  const yLabels = useMemo(() => {
    const mid = (yMin + yMax) / 2;
    return [yMax, mid, yMin];
  }, [yMin, yMax]);

  return (
    <section
      data-testid="pnl-baseline-chart"
      role="region"
      aria-label="Cumulative P&L vs baselines"
      className="flex w-full flex-col gap-3 rounded-xl border border-[var(--ab-moss)]/30 bg-[var(--ab-bg-2)]/60 p-4 text-[var(--ab-text)] sm:p-5"
    >
      <header className="flex flex-wrap items-baseline justify-between gap-3 font-mono text-[11px] uppercase tracking-[0.22em] text-[var(--ab-dim)]">
        <span>cumulative p&amp;l · agent vs baselines</span>
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
          aria-label="Cumulative P&L trajectories: the agent and its baselines"
        >
          {/* Y-axis gridlines */}
          {[1, 0.5, 0].map((p, i) => {
            const y = PADDING.top + (1 - p) * INNER_H;
            return (
              <g key={p}>
                <line
                  x1={PADDING.left}
                  x2={VIEWBOX_W - PADDING.right}
                  y1={y}
                  y2={y}
                  stroke={palette.axis}
                  strokeOpacity={p === 0 || p === 1 ? 0.35 : 0.12}
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
                  {moneyAxis(yLabels[i]!)}
                </text>
              </g>
            );
          })}

          {/* Break-even reference line */}
          {baselineY >= yMin && baselineY <= yMax ? (
            <g data-testid="pnl-baseline-start-ref">
              <line
                x1={PADDING.left}
                x2={VIEWBOX_W - PADDING.right}
                y1={projectY(baselineY, yMin, yMax)}
                y2={projectY(baselineY, yMin, yMax)}
                stroke={palette.baselineRef}
                strokeOpacity={0.35}
                strokeWidth={1}
                strokeDasharray="2 4"
              />
              <text
                x={VIEWBOX_W - PADDING.right - 4}
                y={projectY(baselineY, yMin, yMax) - 4}
                fill={palette.axis}
                fontSize={9}
                textAnchor="end"
                fontFamily="ui-monospace, monospace"
              >
                break-even
              </text>
            </g>
          ) : null}

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
          {ordered.map((s, i) => {
            const color = colorFor(s);
            return (
              <path
                key={s.key}
                data-testid={`pnl-line-${s.key}`}
                d={paths[i]}
                fill="none"
                stroke={color}
                strokeWidth={s.hero ? 2.75 : 1.5}
                opacity={s.hero ? 1 : 0.8}
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            );
          })}

          {/* Vertical scrubber indicator */}
          <line
            data-testid="pnl-baseline-indicator"
            x1={indicatorX}
            x2={indicatorX}
            y1={PADDING.top}
            y2={VIEWBOX_H - PADDING.bottom}
            stroke={palette.indicator}
            strokeWidth={1.5}
            strokeDasharray="4 4"
            opacity={0.85}
          />

          {/* Per-series dots at the active sample */}
          {ordered.map((s) => {
            const v = s.values[clampedIdx] ?? s.values[s.values.length - 1] ?? baselineY;
            const x = PADDING.left + (clampedIdx / Math.max(lastIdx, 1)) * INNER_W;
            const color = colorFor(s);
            return (
              <circle
                key={s.key}
                data-testid={`pnl-current-dot-${s.key}`}
                cx={x}
                cy={projectY(v, yMin, yMax)}
                r={s.hero ? 4.5 : 3}
                fill={color}
                stroke={palette.dotStroke}
                strokeWidth={1.5}
                opacity={s.hero ? 1 : 0.8}
              />
            );
          })}

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

      {/* Legend with the current cumulative-P&L readout per series. */}
      <ul
        data-testid="pnl-baseline-legend"
        className="grid grid-cols-1 gap-x-4 gap-y-2 font-mono text-[10px] uppercase tracking-[0.18em] text-[var(--ab-dim)] sm:grid-cols-2 lg:grid-cols-4"
      >
        {[...viewModel.series]
          .sort((a, b) => (a.hero === b.hero ? 0 : a.hero ? -1 : 1))
          .map((s) => {
            const v = s.values[clampedIdx] ?? s.values[s.values.length - 1] ?? baselineY;
            const color = colorFor(s);
            return (
              <li
                key={s.key}
                data-testid={`pnl-legend-${s.key}`}
                className="flex flex-col gap-1 border-l border-[var(--ab-moss)]/20 pl-3 first:border-l-0 first:pl-0 lg:border-l lg:pl-3"
              >
                <span className="flex items-center gap-2 truncate text-[var(--ab-text)]">
                  <span
                    aria-hidden
                    className="inline-block h-[2px] w-4 flex-none rounded-full"
                    style={{ backgroundColor: color, opacity: s.hero ? 1 : 0.8 }}
                  />
                  <span className="truncate">{s.label}</span>
                </span>
                <span
                  data-testid={`pnl-bankroll-${s.key}`}
                  className="text-sm normal-case"
                  style={{ color: v >= baselineY ? palette.win : palette.loss }}
                >
                  {moneySigned(v)}
                </span>
              </li>
            );
          })}
      </ul>
    </section>
  );
}

function moneyAxis(n: number): string {
  const r = Math.round(n);
  return `${r < 0 ? "−" : ""}$${Math.abs(r).toLocaleString()}`;
}

function moneySigned(n: number): string {
  return `${n < 0 ? "−" : "+"}$${Math.abs(n).toLocaleString(undefined, {
    maximumFractionDigits: 0,
  })}`;
}

export default PnLBaselineChart;
