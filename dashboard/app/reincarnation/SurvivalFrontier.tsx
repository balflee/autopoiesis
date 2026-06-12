"use client";

/**
 * SurvivalFrontier — the groundhog experiment's hero chart: one dot per
 * incarnation (x = incarnation #, y = how far through the season that life
 * got, in % of train markets), dead lives dim, the surviving life glowing at
 * 100%. The staircase TOWARD the finish line — or the plateau that never
 * reaches it — IS the learning verdict, visible at a glance.
 *
 * Pure SVG, presentational only, abyss design vars.
 */

import type { JSX } from "react";

import type { ReincarnationIncarnation } from "@/lib/load_reincarnation";

const W = 800;
const H = 280;
const PAD = 18;

export function SurvivalFrontier({
  incarnations,
}: {
  readonly incarnations: readonly ReincarnationIncarnation[];
}): JSX.Element {
  const n = Math.max(1, incarnations.length);
  const sx = (k: number): number =>
    PAD + ((k - 1) / Math.max(1, n - 1)) * (W - 2 * PAD);
  const sy = (pct: number): number =>
    H - PAD - (Math.min(100, Math.max(0, pct)) / 100) * (H - 2 * PAD);

  return (
    <svg
      data-testid="reincarnation-frontier"
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      role="img"
      aria-label="how far each incarnation survived into the season"
      className="h-64 w-full"
    >
      {/* The finish line — surviving the WHOLE season. */}
      <line
        x1={PAD}
        x2={W - PAD}
        y1={sy(100)}
        y2={sy(100)}
        stroke="var(--ab-glow)"
        strokeOpacity={0.4}
        strokeWidth={1}
        strokeDasharray="3 6"
      />
      {/* The frontier path connecting the death points. */}
      {incarnations.length > 1 ? (
        <polyline
          points={incarnations
            .map((inc) => `${sx(inc.incarnation)},${sy(inc.progress_pct)}`)
            .join(" ")}
          fill="none"
          stroke="var(--ab-dim)"
          strokeOpacity={0.45}
          strokeWidth={1}
          vectorEffect="non-scaling-stroke"
        />
      ) : null}
      {incarnations.map((inc) => (
        <circle
          key={inc.incarnation}
          data-testid={`frontier-dot-${inc.incarnation}`}
          cx={sx(inc.incarnation)}
          cy={sy(inc.progress_pct)}
          r={inc.died ? 3.5 : 6}
          fill={inc.died ? "var(--ab-death)" : "var(--ab-glow)"}
          fillOpacity={inc.died ? 0.55 : 1}
        />
      ))}
    </svg>
  );
}

export default SurvivalFrontier;
