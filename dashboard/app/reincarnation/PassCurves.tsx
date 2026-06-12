"use client";

/**
 * PassCurves — the Phase-2 overlay chart: every training pass's cumulative
 * P&L over its settled-step index, drawn on SHARED axes so the pass-over-pass
 * shape comparison is honest (same x = same nth settlement, same y = same
 * dollars), plus the learning-frozen holdout pass as a dashed overlay.
 *
 * Passes render in rising glow opacity (earliest faintest) — the "lives
 * remembered" motif. Pure SVG polylines, mirroring the BreathWaveform idiom;
 * presentational only.
 */

import type { JSX } from "react";

import type { ReincarnationCurvePoint } from "@/lib/load_reincarnation";

export interface PassCurveSeries {
  readonly label: string;
  readonly points: readonly ReincarnationCurvePoint[];
  /** 0..1 stroke opacity (pass recency). */
  readonly opacity: number;
  /** Dashed = the frozen holdout overlay. */
  readonly dashed?: boolean;
}

const W = 800;
const H = 280;
const PAD = 8;

export function PassCurves({
  series,
}: {
  readonly series: readonly PassCurveSeries[];
}): JSX.Element {
  const xMax = Math.max(
    1,
    ...series.map((s) => s.points[s.points.length - 1]?.i ?? 0),
  );
  const ys = series.flatMap((s) => s.points.map((p) => p.cum_pnl));
  const yMin = Math.min(0, ...ys);
  const yMax = Math.max(1, ...ys);
  const sx = (i: number): number => PAD + (i / xMax) * (W - 2 * PAD);
  const sy = (v: number): number =>
    H - PAD - ((v - yMin) / (yMax - yMin)) * (H - 2 * PAD);

  return (
    <svg
      data-testid="reincarnation-chart"
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      role="img"
      aria-label="cumulative P&L per reincarnation pass on shared axes"
      className="h-64 w-full"
    >
      {/* The zero line — profit above, drowning below. */}
      <line
        x1={PAD}
        x2={W - PAD}
        y1={sy(0)}
        y2={sy(0)}
        stroke="var(--ab-dim)"
        strokeOpacity={0.35}
        strokeWidth={1}
      />
      {series.map((s) =>
        s.points.length === 0 ? null : (
          <polyline
            key={s.label}
            data-testid={`reincarnation-curve-${s.label}`}
            points={s.points.map((p) => `${sx(p.i)},${sy(p.cum_pnl)}`).join(" ")}
            fill="none"
            stroke={s.dashed ? "var(--ab-text)" : "var(--ab-glow)"}
            strokeOpacity={s.opacity}
            strokeWidth={s.dashed ? 1.5 : 2}
            strokeDasharray={s.dashed ? "6 5" : undefined}
            vectorEffect="non-scaling-stroke"
          />
        ),
      )}
    </svg>
  );
}

export default PassCurves;
