"use client";

/**
 * SurvivalVitalsWidget — the BREATH / vitals gauge (E2).
 *
 * Driven by each playback step's `breath` (via `vitalsForStep`), it reads as a
 * cardiac monitor: a breath bar scaled to the CURRENT life's peak breath, the
 * raw breath reading, rolling win-rate, and the breath-waveform motif borrowed
 * from the roadmap landing. As breath drains toward a death the gauge tips into
 * the permadeath color and the trace flatlines — the agent is suffocating.
 *
 * Pure presentational: it takes an already-computed {@link SurvivalVitals}
 * snapshot from the parent (which owns the scrub state). Abyssal-scoped tokens.
 */

import type { JSX } from "react";

import type { SurvivalVitals } from "@/lib/load_survival_journey";

/** A flat baseline with a QRS spike — the cardiac trace, tiled across the box. */
const ECG_POINTS =
  "0,30 70,30 90,30 100,12 110,48 122,8 134,30 210,30 290,30 310,30 320,14 330,46 342,6 354,30 430,30 510,30 530,30 540,12 550,48 562,8 574,30 700,30";

/** A flat (dying) trace — barely a ripple. */
const FLATLINE_POINTS = "0,30 320,30 330,33 340,27 350,30 700,30";

export interface SurvivalVitalsWidgetProps {
  readonly vitals: SurvivalVitals | null;
  /** Total lives in the season (for the "life N / M" label). */
  readonly totalLives: number;
}

const money = (n: number): string =>
  `${n < 0 ? "−" : ""}$${Math.abs(n).toLocaleString(undefined, {
    maximumFractionDigits: 0,
  })}`;

export function SurvivalVitalsWidget({
  vitals,
  totalLives,
}: SurvivalVitalsWidgetProps): JSX.Element {
  const breathPct = vitals ? Math.round(vitals.breathFrac * 100) : 0;
  const danger = vitals?.inDanger ?? false;
  const dying = vitals?.dying ?? false;

  const barColor = dying
    ? "var(--ab-death)"
    : danger
      ? "var(--ab-death)"
      : "var(--ab-glow)";

  const status = dying ? "EXPIRED" : danger ? "SUFFOCATING" : "BREATHING";

  return (
    <section
      data-testid="survival-vitals"
      data-status={status.toLowerCase()}
      data-danger={danger ? "true" : "false"}
      role="region"
      aria-label="Agent vitals — breath"
      className="flex w-full flex-col gap-4 rounded-xl border border-[var(--ab-moss)]/30 bg-[var(--ab-bg-2)]/60 p-4 text-[var(--ab-text)] sm:p-5"
    >
      <header className="flex flex-wrap items-baseline justify-between gap-2 font-mono text-[10px] uppercase tracking-[0.28em] text-[var(--ab-dim)]">
        <span>vitals · breath</span>
        <span
          data-testid="survival-vitals-status"
          className="font-mono"
          style={{ color: danger || dying ? "var(--ab-death)" : "var(--ab-glow)" }}
        >
          {status}
        </span>
      </header>

      {/* The cardiac trace — glow when alive, flatline when expiring. */}
      <div className="h-12 w-full overflow-hidden">
        <svg
          data-testid="survival-vitals-waveform"
          viewBox="0 0 700 60"
          preserveAspectRatio="none"
          className="h-full w-full"
          role="img"
          aria-label={dying ? "flatlining" : "heartbeat"}
        >
          <polyline
            className={dying ? "" : "ab-breath-line"}
            points={dying ? FLATLINE_POINTS : ECG_POINTS}
            fill="none"
            stroke={dying ? "var(--ab-death)" : "var(--ab-glow)"}
            strokeWidth={2}
            opacity={dying ? 0.7 : 1}
          />
        </svg>
      </div>

      {/* Breath bar — scaled to this life's peak breath. */}
      <div className="flex flex-col gap-1.5">
        <div
          className="relative h-2.5 w-full overflow-hidden rounded-full"
          style={{ backgroundColor: "rgba(159,179,169,0.16)" }}
        >
          <div
            data-testid="survival-breath-bar"
            className="h-full rounded-full transition-[width] duration-150 ease-out"
            style={{
              width: `${breathPct}%`,
              backgroundColor: barColor,
              boxShadow:
                danger || dying
                  ? "0 0 10px rgba(255,107,74,0.5)"
                  : "0 0 12px rgba(200,249,76,0.45)",
            }}
          />
        </div>
        <div className="flex items-baseline justify-between font-mono text-[10px] uppercase tracking-[0.18em] text-[var(--ab-dim)]">
          <span data-testid="survival-breath-reading">
            breath{" "}
            <span style={{ color: danger || dying ? "var(--ab-death)" : "var(--ab-text)" }}>
              {vitals ? vitals.breath.toFixed(0) : "—"}
            </span>
          </span>
          <span>
            {breathPct}% of life peak
          </span>
        </div>
      </div>

      {/* Secondary vitals readout. */}
      <dl className="grid grid-cols-3 gap-3 border-t border-[var(--ab-moss)]/20 pt-3 font-mono text-[10px] uppercase tracking-[0.18em] text-[var(--ab-dim)]">
        <div className="flex flex-col gap-0.5">
          <dt>life</dt>
          <dd className="text-sm normal-case text-[var(--ab-text)]">
            {vitals ? vitals.lifeIdx : "—"}
            <span className="text-[var(--ab-dim)]"> / {Math.max(totalLives - 1, 0)}</span>
          </dd>
        </div>
        <div className="flex flex-col gap-0.5">
          <dt>win rate</dt>
          <dd className="text-sm normal-case text-[var(--ab-text)]">
            {vitals ? `${Math.round(vitals.winRate * 100)}%` : "—"}
          </dd>
        </div>
        <div className="flex flex-col gap-0.5">
          <dt>cum p&amp;l</dt>
          <dd
            data-testid="survival-vitals-cum"
            className="text-sm normal-case"
            style={{ color: (vitals?.cumPnl ?? 0) >= 0 ? "var(--ab-glow)" : "var(--ab-death)" }}
          >
            {vitals ? money(vitals.cumPnl) : "—"}
          </dd>
        </div>
      </dl>
    </section>
  );
}

export default SurvivalVitalsWidget;
