"use client";

/**
 * EvolutionCurve — PRD §8 right-bottom panel.
 *
 * Two overlaid trajectories on a shared time axis:
 *
 *   1. Cumulative win rate (0..1) — derived from settled decision_feed
 *      rows (WIN/LOSS). Rendered as a primary line in WIN green.
 *   2. W_R + W_S weight trajectories — overlaid as a thin band so the
 *      audience sees the engine balance migrate as the Agent learns.
 *
 * Plus two annotation markers:
 *   - β₁ activation marker (the first weights_history point where
 *     beta > 1e-6) — vertical AMBER tick + label "LLM ONLINE". This is
 *     the demo's "sentient layer awakens" beat from PRD §9.
 *   - Phase boundary markers from the latest phase_transition entry —
 *     vertical INK tick + label "P1→P2" (etc.).
 *
 * Implementation: bespoke SVG. We deliberately avoid pulling in
 * Recharts (180 kB minified, drops Lighthouse perf below the gate)
 * for a chart this simple — the visual brief is a line + a band +
 * two annotations, all of which are trivial path math.
 *
 * Loading state: when no settled rows exist yet, render an empty
 * chart skeleton with a "waiting for first settled trade" caption.
 */

import { useMemo, type JSX } from "react";

import { ColorTokens } from "@/lib/colorTokens";
import {
  selectCumulativePnl,
  selectWeightsHistory,
  useWsStore,
  type CumulativePnlPoint,
  type WeightsHistoryPoint,
} from "@/lib/wsStore";

const VIEWBOX_W = 600;
const VIEWBOX_H = 220;
const PADDING = { top: 18, right: 18, bottom: 32, left: 36 };
const INNER_W = VIEWBOX_W - PADDING.left - PADDING.right;
const INNER_H = VIEWBOX_H - PADDING.top - PADDING.bottom;

const BETA_FROZEN_THRESHOLD = 1e-6;

interface Point {
  readonly x: number;
  readonly y: number;
}

function tsToMillis(ts: string): number {
  const n = Date.parse(ts);
  return Number.isFinite(n) ? n : 0;
}

/** Map a list of (x_ms, y_value 0..1) points to SVG-space points. */
function project(
  data: ReadonlyArray<{ ts: string; value: number }>,
  xMin: number,
  xMax: number,
): readonly Point[] {
  if (data.length === 0) return [];
  const xRange = Math.max(xMax - xMin, 1);
  return data.map((d) => {
    const xPct = (tsToMillis(d.ts) - xMin) / xRange;
    const x = PADDING.left + xPct * INNER_W;
    const y =
      PADDING.top +
      (1 - Math.max(0, Math.min(1, d.value))) * INNER_H;
    return { x, y };
  });
}

function pathFromPoints(pts: readonly Point[]): string {
  if (pts.length === 0) return "";
  return pts
    .map((p, i) =>
      i === 0
        ? `M ${p.x.toFixed(1)} ${p.y.toFixed(1)}`
        : `L ${p.x.toFixed(1)} ${p.y.toFixed(1)}`,
    )
    .join(" ");
}

export function EvolutionCurve(): JSX.Element {
  const pnl = useWsStore(selectCumulativePnl);
  const wHistory = useWsStore(selectWeightsHistory);
  const phaseTransition = useWsStore((s) => s.phaseTransition);

  const { winRatePath, wrPath, wsPath, betaMarker, phaseMarkerX, xMin, xMax, latestWinRate, latestWins, latestLosses } =
    useMemo(() => {
      const allTs: string[] = [
        ...pnl.map((p) => p.ts),
        ...wHistory.map((p) => p.ts),
        phaseTransition?.ts ?? "",
      ].filter(Boolean);
      if (allTs.length === 0) {
        return {
          winRatePath: "",
          wrPath: "",
          wsPath: "",
          betaMarker: null as number | null,
          phaseMarkerX: null as number | null,
          xMin: 0,
          xMax: 0,
          latestWinRate: 0,
          latestWins: 0,
          latestLosses: 0,
        };
      }
      const xMs = allTs.map(tsToMillis);
      const lo = Math.min(...xMs);
      const hi = Math.max(...xMs);

      const winRatePts = project(
        pnl.map((p) => ({ ts: p.ts, value: p.win_rate })),
        lo,
        hi,
      );

      const wrPts = project(
        wHistory.map((p) => ({ ts: p.ts, value: p.w_r })),
        lo,
        hi,
      );
      const wsPts = project(
        wHistory.map((p) => ({ ts: p.ts, value: p.w_s })),
        lo,
        hi,
      );

      // First weights point where beta crossed the unfrozen threshold.
      const firstUnfrozen = wHistory.find(
        (p: WeightsHistoryPoint) => p.beta > BETA_FROZEN_THRESHOLD,
      );
      let betaMarkerX: number | null = null;
      if (firstUnfrozen) {
        const xPct = (tsToMillis(firstUnfrozen.ts) - lo) / Math.max(hi - lo, 1);
        betaMarkerX = PADDING.left + xPct * INNER_W;
      }

      let phaseMarkerXCalc: number | null = null;
      if (phaseTransition) {
        const xPct = (tsToMillis(phaseTransition.ts) - lo) / Math.max(hi - lo, 1);
        phaseMarkerXCalc = PADDING.left + xPct * INNER_W;
      }

      const latest = pnl[pnl.length - 1] as CumulativePnlPoint | undefined;

      return {
        winRatePath: pathFromPoints(winRatePts),
        wrPath: pathFromPoints(wrPts),
        wsPath: pathFromPoints(wsPts),
        betaMarker: betaMarkerX,
        phaseMarkerX: phaseMarkerXCalc,
        xMin: lo,
        xMax: hi,
        latestWinRate: latest?.win_rate ?? 0,
        latestWins: latest?.wins ?? 0,
        latestLosses: latest?.losses ?? 0,
      };
    }, [pnl, wHistory, phaseTransition]);

  const hasData = pnl.length > 0 || wHistory.length > 0;

  return (
    <section
      data-testid="evolution-curve"
      data-loading={hasData ? "false" : "true"}
      role="region"
      aria-label="Evolution curve — cumulative win rate and weight trajectories"
      className="flex w-full flex-col gap-3 rounded-lg border border-genesis-ink-muted/30 bg-genesis-bg p-4 text-genesis-ink sm:p-6"
    >
      <header className="flex flex-wrap items-baseline justify-between gap-3 font-mono text-xs uppercase tracking-[0.2em] text-genesis-ink-muted">
        <span>evolution</span>
        <span
          data-testid="evolution-curve-win-rate-readout"
          className="text-genesis-ink"
        >
          win rate{" "}
          <span style={{ color: ColorTokens.WIN }}>
            {(latestWinRate * 100).toFixed(0)}%
          </span>{" "}
          ({latestWins}–{latestLosses})
        </span>
      </header>

      <div className="w-full overflow-hidden">
        <svg
          viewBox={`0 0 ${VIEWBOX_W} ${VIEWBOX_H}`}
          preserveAspectRatio="xMidYMid meet"
          className="block w-full"
          role="img"
          aria-label="Cumulative win rate and dual-engine weight trajectory chart"
        >
          {/* Axis frame */}
          <line
            x1={PADDING.left}
            x2={VIEWBOX_W - PADDING.right}
            y1={VIEWBOX_H - PADDING.bottom}
            y2={VIEWBOX_H - PADDING.bottom}
            stroke={ColorTokens.INK_MUTED}
            strokeOpacity={0.4}
            strokeWidth={1}
          />
          <line
            x1={PADDING.left}
            x2={PADDING.left}
            y1={PADDING.top}
            y2={VIEWBOX_H - PADDING.bottom}
            stroke={ColorTokens.INK_MUTED}
            strokeOpacity={0.4}
            strokeWidth={1}
          />

          {/* Y-axis labels: 0%, 50%, 100% */}
          {[0, 0.5, 1].map((p) => {
            const y = PADDING.top + (1 - p) * INNER_H;
            return (
              <g key={p}>
                <line
                  x1={PADDING.left}
                  x2={VIEWBOX_W - PADDING.right}
                  y1={y}
                  y2={y}
                  stroke={ColorTokens.INK_MUTED}
                  strokeOpacity={0.1}
                  strokeWidth={1}
                />
                <text
                  x={PADDING.left - 6}
                  y={y + 3}
                  fill={ColorTokens.INK_MUTED}
                  fontSize={9}
                  textAnchor="end"
                  fontFamily="ui-monospace, monospace"
                >
                  {(p * 100).toFixed(0)}%
                </text>
              </g>
            );
          })}

          {/* W_R + W_S trajectory band */}
          {wrPath && (
            <path
              data-testid="evolution-curve-wr-path"
              d={wrPath}
              fill="none"
              stroke={ColorTokens.WIN}
              strokeOpacity={0.35}
              strokeWidth={1.5}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          )}
          {wsPath && (
            <path
              data-testid="evolution-curve-ws-path"
              d={wsPath}
              fill="none"
              stroke={ColorTokens.AMBER}
              strokeOpacity={0.35}
              strokeWidth={1.5}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          )}

          {/* Cumulative win rate — primary line */}
          {winRatePath && (
            <path
              data-testid="evolution-curve-winrate-path"
              d={winRatePath}
              fill="none"
              stroke={ColorTokens.WIN}
              strokeWidth={2.5}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          )}

          {/* β₁ activation marker (vertical line + label) */}
          {betaMarker !== null && (
            <g data-testid="evolution-curve-beta-marker">
              <line
                x1={betaMarker}
                x2={betaMarker}
                y1={PADDING.top}
                y2={VIEWBOX_H - PADDING.bottom}
                stroke={ColorTokens.AMBER}
                strokeWidth={1.5}
                strokeDasharray="3 3"
              />
              <text
                x={betaMarker + 4}
                y={PADDING.top + 10}
                fill={ColorTokens.AMBER}
                fontSize={10}
                fontFamily="ui-monospace, monospace"
              >
                LLM ONLINE
              </text>
            </g>
          )}

          {/* Phase transition marker */}
          {phaseMarkerX !== null && phaseTransition && (
            <g data-testid="evolution-curve-phase-marker">
              <line
                x1={phaseMarkerX}
                x2={phaseMarkerX}
                y1={PADDING.top}
                y2={VIEWBOX_H - PADDING.bottom}
                stroke={ColorTokens.INK}
                strokeOpacity={0.6}
                strokeWidth={1}
                strokeDasharray="2 4"
              />
              <text
                x={phaseMarkerX + 4}
                y={VIEWBOX_H - PADDING.bottom - 4}
                fill={ColorTokens.INK}
                fontSize={10}
                fontFamily="ui-monospace, monospace"
              >
                {abbreviatePhase(phaseTransition.payload.from)}→
                {abbreviatePhase(phaseTransition.payload.to)}
              </text>
            </g>
          )}

          {/* Time-range labels (left + right) */}
          {hasData && (
            <>
              <text
                x={PADDING.left}
                y={VIEWBOX_H - PADDING.bottom + 18}
                fill={ColorTokens.INK_MUTED}
                fontSize={9}
                fontFamily="ui-monospace, monospace"
              >
                {formatClock(xMin)}
              </text>
              <text
                x={VIEWBOX_W - PADDING.right}
                y={VIEWBOX_H - PADDING.bottom + 18}
                fill={ColorTokens.INK_MUTED}
                fontSize={9}
                textAnchor="end"
                fontFamily="ui-monospace, monospace"
              >
                {formatClock(xMax)}
              </text>
            </>
          )}

          {/* Empty-state caption */}
          {!hasData && (
            <text
              data-testid="evolution-curve-empty"
              x={VIEWBOX_W / 2}
              y={VIEWBOX_H / 2}
              fill={ColorTokens.INK_MUTED}
              fontSize={11}
              textAnchor="middle"
              fontFamily="ui-monospace, monospace"
            >
              waiting for first settled trade…
            </text>
          )}
        </svg>
      </div>

      {/* Legend */}
      <ul className="flex flex-wrap gap-x-5 gap-y-1 font-mono text-[10px] uppercase tracking-[0.18em] text-genesis-ink-muted">
        <li className="flex items-center gap-2">
          <span
            aria-hidden
            className="inline-block h-[2px] w-5 rounded-full"
            style={{ backgroundColor: ColorTokens.WIN }}
          />
          win rate
        </li>
        <li className="flex items-center gap-2">
          <span
            aria-hidden
            className="inline-block h-[2px] w-5 rounded-full opacity-50"
            style={{ backgroundColor: ColorTokens.WIN }}
          />
          W_R
        </li>
        <li className="flex items-center gap-2">
          <span
            aria-hidden
            className="inline-block h-[2px] w-5 rounded-full opacity-50"
            style={{ backgroundColor: ColorTokens.AMBER }}
          />
          W_S
        </li>
      </ul>
    </section>
  );
}

function abbreviatePhase(p: string): string {
  switch (p) {
    case "PHASE_1_INFANCY":
      return "P1";
    case "PHASE_2_APPRENTICE":
      return "P2";
    case "PHASE_3_MASTER":
      return "P3";
    case "PHASE_4_TERMINAL":
      return "P4";
    default:
      return "?";
  }
}

function formatClock(ms: number): string {
  if (!Number.isFinite(ms) || ms <= 0) return "—";
  const d = new Date(ms);
  const h = d.getUTCHours().toString().padStart(2, "0");
  const m = d.getUTCMinutes().toString().padStart(2, "0");
  return `${h}:${m}`;
}

export default EvolutionCurve;
