"use client";

/**
 * CountdownWidget — T-D-007 acceptance.
 *
 * Live countdown to projected energy depletion, ticking at 1 s while
 * the dashboard is mounted. Reads BREATH + effective_burn_rate from
 * the wsStore (vitals.breath + vitals.gas_per_min) and projects the
 * remaining seconds via the pure `computeCountdown` calculator.
 *
 * Visual tiers (driven by `tier` from the calculator):
 *   - safe        : muted ink, normal weight
 *   - warning     : amber, bold (≤ 10 min)
 *   - critical    : loss-red, bold (≤ 5 min)
 *   - imminent    : loss-red, bold + pulse (≤ 1 min)
 *   - expired     : loss-red, 00:00 fixed
 *
 * Why a SECOND ticker (vitals already ships a countdown_s field):
 *   - `countdown_s` in vitals is "seconds to next decision tick" —
 *     a different semantic from "seconds until death". The brief
 *     wants the latter for the Death Watch §9 climax, so we project
 *     it from BREATH + burn rate independently.
 *   - The pure calculator means the unit tests in
 *     death_watch_thresholds.test.ts cover the math without rendering.
 *
 * CLS protection: width-stable container (font-variant-numeric:
 * tabular-nums + reserved character slots) so the bigger tiers do
 * not push surrounding text around. AAA contrast (16:1) maintained
 * via genesis-ink colour for safe; loss-red @ 24px+ stays in AA Large.
 */

import { useEffect, useState, type JSX } from "react";

import { ColorTokens } from "@/lib/colorTokens";
import {
  computeCountdown,
  type CountdownResult,
  type CountdownTier,
} from "@/lib/death_watch_thresholds";
import { useWsStore } from "@/lib/wsStore";

export interface CountdownWidgetProps {
  /**
   * Override the 1-s tick cadence. Defaults to 1000 ms; tests pass
   * smaller values to keep specs fast. Setting 0 disables the timer
   * entirely (useful in unit tests where we manually flush vitals).
   */
  readonly tickMs?: number;
  /** Optional injected `Date.now` for deterministic tests. */
  readonly nowFn?: () => number;
}

/**
 * Map a tier name to the colour token used for the countdown numerals.
 * Mirrors the death_watch.css `data-tier` selectors below — keeping
 * both in sync because Playwright asserts both the data-attr AND the
 * computed style colour in `death_watch.spec.ts`.
 */
const TIER_COLOR: Record<CountdownTier, string> = {
  safe: ColorTokens.INK_MUTED,
  warning: ColorTokens.AMBER,
  critical: ColorTokens.LOSS,
  imminent: ColorTokens.LOSS,
  expired: ColorTokens.LOSS,
};

const TIER_LABEL: Record<CountdownTier, string> = {
  safe: "projected lifespan",
  warning: "burn rate critical",
  critical: "energy reserves critical",
  imminent: "imminent depletion",
  expired: "agent terminated",
};

export function CountdownWidget(props: CountdownWidgetProps = {}): JSX.Element {
  const breath = useWsStore((s) => s.vitals?.breath ?? null);
  const burnRate = useWsStore((s) => s.vitals?.gas_per_min ?? null);
  const causeOfDeath = useWsStore((s) => s.causeOfDeath);

  // The store-derived result is the source of truth; we re-derive on every
  // tick so the seconds count keeps moving even when no new vitals frame
  // lands. Between vitals frames we extrapolate from the last known burn
  // rate — same approach the on-chain Death Watch uses (extrapolate from
  // last `recordBurn`).
  const tickMs = props.tickMs ?? 1000;
  const nowFn = props.nowFn ?? Date.now;
  const [tick, setTick] = useState(0);
  const [anchorAt, setAnchorAt] = useState(() => nowFn());
  const [anchorBreath, setAnchorBreath] = useState<number | null>(breath);

  // Re-anchor whenever vitals push a fresh breath value.
  useEffect(() => {
    if (breath == null) return;
    setAnchorBreath(breath);
    setAnchorAt(nowFn());
  }, [breath, nowFn]);

  // 1-s ticker — purely visual, drives the `tick` state to force a
  // re-render so computeCountdown re-runs against the elapsed time.
  useEffect(() => {
    if (tickMs <= 0) return;
    const id = setInterval(() => setTick((t) => (t + 1) % 1_000_000), tickMs);
    return () => clearInterval(id);
  }, [tickMs]);

  // Quiet `tick`-unused lint; the value is read implicitly via state
  // updates rerunning this function on the next render.
  void tick;

  // Project current breath from the anchor + elapsed time + burn rate.
  // If we never received a vitals frame, fall through to "expired"
  // semantics from the calculator (which renders 00:00 sensibly).
  let result: CountdownResult;
  if (anchorBreath == null || burnRate == null) {
    result = computeCountdown(0, 0);
  } else {
    const elapsedMin = Math.max(0, (nowFn() - anchorAt) / 60_000);
    const decayed = anchorBreath - burnRate * elapsedMin;
    result = computeCountdown(decayed, burnRate);
  }

  const tier: CountdownTier = causeOfDeath ? "expired" : result.tier;
  const formatted = causeOfDeath ? "00:00" : result.formatted;
  const color = TIER_COLOR[tier];
  const label = TIER_LABEL[tier];

  return (
    <div
      data-testid="countdown-widget"
      data-tier={tier}
      data-seconds={result.seconds_remaining}
      data-formatted={formatted}
      role="timer"
      // Politeness chosen so the demo's screen-reader path narrates
      // ONLY at meaningful tier transitions, not every second.
      aria-live="polite"
      aria-atomic="true"
      aria-label={`${label}: ${formatted}`}
      className="genesis-countdown-widget flex flex-col items-center gap-1 px-3 py-2"
    >
      <span
        data-testid="countdown-widget-label"
        className="font-mono text-[10px] uppercase tracking-[0.3em] text-genesis-ink-muted"
      >
        {label}
      </span>
      <span
        data-testid="countdown-widget-value"
        className="font-mono text-3xl font-bold tabular-nums sm:text-4xl"
        style={{
          color,
          // Force tabular numerals — the imminent tier oscillates the
          // seconds digit every tick; without this the chrome jitters.
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {formatted}
      </span>
    </div>
  );
}

export default CountdownWidget;
