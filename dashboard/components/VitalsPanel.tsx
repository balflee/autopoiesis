"use client";

/**
 * VitalsPanel — top-of-dashboard "agent dashboard / cockpit" strip.
 *
 * PRD §8 calls for: BREATH bar, bankroll bar, countdown, Gas rate,
 * Phase badge. This is the first thing the audience sees, so it has
 * to read clearly at 375px and from a projector — large numerals,
 * AAA contrast, no decorative filigree.
 *
 * Data lives in `useWsStore`; mocks inject by calling `ingest(...)`
 * in tests. When `vitals === null` (the "WS not yet connected" case)
 * we render the skeleton so the layout doesn't pop in.
 */

import type { JSX } from "react";

import { widgetPalette, type WidgetVariant } from "@/lib/colorTokens";
import { useWsStore } from "@/lib/wsStore";
import type { AgentPhase } from "@/lib/types";

const PHASE_LABELS: Record<AgentPhase, string> = {
  PHASE_1_INFANCY: "Phase 1 · Infancy",
  PHASE_2_APPRENTICE: "Phase 2 · Apprenticeship",
  PHASE_3_MASTER: "Phase 3 · Mastery",
  PHASE_4_TERMINAL: "Phase 4 · Terminal Lucidity",
};

/** Phase accent colors, resolved per theme variant (navy = legacy literals). */
function phaseAccentFor(variant: WidgetVariant): Record<AgentPhase, string> {
  const p = widgetPalette(variant);
  return {
    PHASE_1_INFANCY: p.inkMuted,
    PHASE_2_APPRENTICE: p.accent2,
    PHASE_3_MASTER: p.accent,
    PHASE_4_TERMINAL: p.danger,
  };
}

// BREATH soft-cap (PRD §4) — used as the bar's full-scale denominator.
const BREATH_FULL = 100;
// Bankroll bar tops out at $300 for the demo so even early Phase 1
// modest gains are visible. Adjust as Track B settles on the cap.
const BANKROLL_BAR_MAX = 300;

export function VitalsPanel({
  variant = "navy",
}: {
  /** Theme variant — `"navy"` (default, legacy) or `"abyss"` (/mock). */
  readonly variant?: WidgetVariant;
} = {}): JSX.Element {
  const vitals = useWsStore((s) => s.vitals);
  const connection = useWsStore((s) => s.connection);
  const pal = widgetPalette(variant);

  if (!vitals) {
    return (
      <section
        data-testid="vitals-panel"
        data-loading="true"
        role="region"
        aria-label="Agent vitals (waiting on backend)"
        className={`flex w-full flex-col gap-3 rounded-lg border p-4 ${pal.panel}`}
      >
        <p className="font-mono text-xs uppercase tracking-[0.2em]">
          waiting for agent stream · {connection}
        </p>
        <SkeletonBar variant={variant} />
        <SkeletonBar variant={variant} />
      </section>
    );
  }

  const breathPct = Math.max(0, Math.min(100, (vitals.breath / BREATH_FULL) * 100));
  const bankrollPct = Math.max(
    0,
    Math.min(100, (vitals.bankroll / BANKROLL_BAR_MAX) * 100),
  );
  const phaseLabel = PHASE_LABELS[vitals.phase];
  const phaseAccent = phaseAccentFor(variant)[vitals.phase];

  // Death-watch threshold — PRD §8 Phase 4 visual: bar turns red ≤10%.
  const breathColor = breathPct <= 10 ? pal.danger : pal.accent;

  return (
    <section
      data-testid="vitals-panel"
      role="region"
      aria-label={`Agent vitals — ${phaseLabel}`}
      className={`flex w-full flex-col gap-4 rounded-lg border p-4 sm:p-6 ${pal.panelSolid}`}
    >
      <header className="flex flex-wrap items-center justify-between gap-3">
        <span
          data-testid="vitals-phase-badge"
          className="rounded border px-3 py-1 font-mono text-xs uppercase tracking-[0.2em]"
          style={{ borderColor: phaseAccent, color: phaseAccent }}
        >
          {phaseLabel}
        </span>
        <span
          data-testid="vitals-connection-badge"
          className={`font-mono text-xs uppercase tracking-[0.2em] ${pal.textMuted}`}
        >
          ws · {connection}
        </span>
      </header>

      <Meter
        testId="vitals-breath"
        label="BREATH"
        value={`${vitals.breath.toFixed(0)} / ${BREATH_FULL}`}
        pct={breathPct}
        color={breathColor}
        variant={variant}
      />

      <Meter
        testId="vitals-bankroll"
        label="BANKROLL"
        value={`$${vitals.bankroll.toFixed(2)}`}
        pct={bankrollPct}
        color={pal.accent2}
        variant={variant}
      />

      <dl className="grid grid-cols-2 gap-x-6 gap-y-2 font-mono text-sm sm:grid-cols-4">
        <Fact
          testId="vitals-countdown"
          label="next decision"
          value={formatCountdown(vitals.countdown_s)}
          variant={variant}
        />
        <Fact
          testId="vitals-gas"
          label="gas / min"
          value={vitals.gas_per_min.toFixed(2)}
          variant={variant}
        />
      </dl>
    </section>
  );
}

function Meter(props: {
  testId: string;
  label: string;
  value: string;
  pct: number;
  color: string;
  variant: WidgetVariant;
}): JSX.Element {
  const pal = widgetPalette(props.variant);
  return (
    <div data-testid={props.testId} className="flex flex-col gap-1">
      <div
        className={`flex items-baseline justify-between font-mono text-xs uppercase tracking-[0.2em] ${pal.textMuted}`}
      >
        <span>{props.label}</span>
        <span
          data-testid={`${props.testId}-value`}
          className={pal.textStrong}
        >
          {props.value}
        </span>
      </div>
      <div
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(props.pct)}
        aria-label={props.label}
        className={`h-3 w-full overflow-hidden rounded-full ${pal.track}`}
      >
        <div
          data-testid={`${props.testId}-fill`}
          className="h-full rounded-full transition-[width] duration-500"
          style={{
            width: `${props.pct}%`,
            backgroundColor: props.color,
          }}
        />
      </div>
    </div>
  );
}

function Fact(props: {
  testId: string;
  label: string;
  value: string;
  variant: WidgetVariant;
}): JSX.Element {
  const pal = widgetPalette(props.variant);
  return (
    <div data-testid={props.testId} className="flex flex-col">
      <dt className={`text-xs uppercase tracking-[0.2em] ${pal.textMuted}`}>
        {props.label}
      </dt>
      <dd className={`text-lg ${pal.textStrong}`}>{props.value}</dd>
    </div>
  );
}

function SkeletonBar({ variant }: { variant: WidgetVariant }): JSX.Element {
  return (
    <div
      className={`h-3 w-full animate-pulse rounded-full ${widgetPalette(variant).track}`}
    />
  );
}

function formatCountdown(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "—";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export default VitalsPanel;
