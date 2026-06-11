"use client";

/**
 * SurvivalMatchCard — the current-match / current-bet card (E2).
 *
 * Reads ONE survival step (the playback position) straight from the journey
 * fixture — never the live WS store. Surfaces the match the agent is grading
 * RIGHT NOW: the two players, surface, the side + size it took, the realised
 * P&L, and the five engine signals that fed the fusion. As the scrubber walks
 * the season this card flips through 842 real bets.
 *
 * Abyssal-scoped, presentational. The parent owns the scrub state and hands the
 * already-selected step.
 */

import type { JSX } from "react";

import {
  SURVIVAL_SIGNAL_KEYS,
  SURVIVAL_SIGNAL_LABEL,
  type SurvivalStep,
} from "@/lib/load_survival_journey";

const SURFACE_ACCENT: Record<string, string> = {
  Hard: "#7DD3FC", // sky
  Clay: "#ff6b4a",
  Grass: "var(--ab-glow)",
  Carpet: "var(--ab-dim)",
};

const money = (n: number): string =>
  `${n < 0 ? "−" : "+"}$${Math.abs(n).toLocaleString(undefined, {
    maximumFractionDigits: 2,
  })}`;

/** Title-case a lowercase surname like "sabalenka" → "Sabalenka". */
function cap(name: string): string {
  if (name.length === 0) return name;
  return name[0]!.toUpperCase() + name.slice(1);
}

/** A small signed signal bar in [-1, 1], glow for +, death for −. */
function SignalBar({ value }: { value: number }): JSX.Element {
  const clamped = Math.max(-1, Math.min(1, value));
  const pct = Math.abs(clamped) * 50; // half-width per side
  const positive = clamped >= 0;
  return (
    <span
      aria-hidden
      className="relative block h-1.5 w-full overflow-hidden rounded-full"
      style={{ backgroundColor: "rgba(159,179,169,0.14)" }}
    >
      {/* center tick */}
      <span className="absolute left-1/2 top-0 h-full w-px -translate-x-1/2 bg-[var(--ab-dim)]/40" />
      <span
        className="absolute top-0 h-full rounded-full"
        style={{
          width: `${pct}%`,
          left: positive ? "50%" : `${50 - pct}%`,
          backgroundColor: positive ? "var(--ab-glow)" : "var(--ab-death)",
          opacity: 0.9,
        }}
      />
    </span>
  );
}

export interface SurvivalMatchCardProps {
  readonly step: SurvivalStep | null;
  /** Global step index, for the header readout. */
  readonly stepIndex: number;
  readonly totalSteps: number;
}

export function SurvivalMatchCard({
  step,
  stepIndex,
  totalSteps,
}: SurvivalMatchCardProps): JSX.Element {
  if (!step) {
    return (
      <section
        data-testid="survival-match-card"
        data-empty="true"
        role="region"
        aria-label="Current match — no bet"
        className="flex h-full w-full flex-col gap-3 rounded-xl border border-[var(--ab-moss)]/30 bg-[var(--ab-bg-2)]/60 p-5 text-[var(--ab-text)]"
      >
        <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-[var(--ab-dim)]">
          no bet on the wire
        </p>
      </section>
    );
  }

  const m = step.market;
  const surfaceColor = SURFACE_ACCENT[m.surface] ?? "var(--ab-dim)";
  const sideColor = step.side === "YES" ? "var(--ab-glow)" : "#7DD3FC";
  const won = step.pnl >= 0;

  return (
    <section
      data-testid="survival-match-card"
      data-market-id={m.market_id}
      role="region"
      aria-label={`Current bet: ${cap(m.players[0])} vs ${cap(m.players[1])} on ${m.surface}`}
      className="flex h-full w-full flex-col gap-4 rounded-xl border border-[var(--ab-moss)]/30 bg-[var(--ab-bg-2)]/60 p-4 text-[var(--ab-text)] sm:p-5"
    >
      <header className="flex items-baseline justify-between font-mono text-[10px] uppercase tracking-[0.28em] text-[var(--ab-dim)]">
        <span>current bet · life {step.life_idx}</span>
        <span data-testid="survival-match-step" className="text-[var(--ab-text)]">
          {stepIndex} / {Math.max(totalSteps - 1, 0)}
        </span>
      </header>

      {/* AI reflection annotation (B3) — present only on AI-run steps where the
          reflect→advisor closure fired. One subtle line; absent otherwise. */}
      {step.reflection ? (
        <p
          data-testid="survival-match-reflection"
          className="flex items-start gap-2 rounded-md border border-[var(--ab-glow)]/30 bg-[var(--ab-glow-soft)] px-2.5 py-1.5 font-mono text-[9px] leading-relaxed tracking-[0.08em] text-[var(--ab-glow)] ab-glow-text"
        >
          <span aria-hidden className="mt-px shrink-0 not-italic">
            ◈
          </span>
          <span className="normal-case text-[var(--ab-text)]">
            <span className="uppercase tracking-[0.18em] text-[var(--ab-glow)]">
              reflection ·{" "}
            </span>
            {step.reflection}
          </span>
        </p>
      ) : null}

      {/* Players */}
      <div className="flex flex-col gap-2">
        <span
          data-testid="survival-match-player-a"
          className="font-display text-2xl leading-none text-[var(--ab-text)] sm:text-3xl"
        >
          {cap(m.players[0])}
        </span>
        <div className="flex items-center gap-3 font-mono text-[9px] uppercase tracking-[0.28em] text-[var(--ab-dim)]">
          <span className="h-px flex-1 bg-[var(--ab-moss)]/40" />
          vs
          <span className="h-px flex-1 bg-[var(--ab-moss)]/40" />
        </div>
        <span
          data-testid="survival-match-player-b"
          className="font-display text-2xl leading-none text-[var(--ab-dim)] sm:text-3xl"
        >
          {cap(m.players[1])}
        </span>
      </div>

      {/* Surface / side / size / pnl */}
      <ul className="grid grid-cols-2 gap-3 border-t border-[var(--ab-moss)]/20 pt-3 font-mono text-[10px] uppercase tracking-[0.18em] text-[var(--ab-dim)]">
        <li className="flex items-center gap-2">
          <span
            aria-hidden
            className="inline-block h-2 w-2 rounded-full"
            style={{ backgroundColor: surfaceColor }}
          />
          <span data-testid="survival-match-surface" className="text-[var(--ab-text)]">
            {m.surface}
          </span>
        </li>
        <li>
          side{" "}
          <span data-testid="survival-match-side" className="normal-case" style={{ color: sideColor }}>
            {step.side}
          </span>
        </li>
        <li>
          stake{" "}
          <span data-testid="survival-match-size" className="text-[var(--ab-text)] normal-case">
            ${step.size.toFixed(2)}
          </span>
        </li>
        <li>
          settled{" "}
          <span
            data-testid="survival-match-pnl"
            className="normal-case"
            style={{ color: won ? "var(--ab-glow)" : "var(--ab-death)" }}
          >
            {money(step.pnl)}
          </span>
        </li>
      </ul>

      {/* The five engine signals that fed the fusion. */}
      <div
        data-testid="survival-match-signals"
        className="flex flex-col gap-2 border-t border-[var(--ab-moss)]/20 pt-3"
      >
        <span className="font-mono text-[9px] uppercase tracking-[0.24em] text-[var(--ab-dim)]">
          engine signals
        </span>
        <ul className="flex flex-col gap-1.5">
          {SURVIVAL_SIGNAL_KEYS.map((k) => {
            const v = step.signals[k];
            return (
              <li
                key={k}
                data-testid={`survival-signal-${k}`}
                className="grid grid-cols-[7.5rem_1fr_3rem] items-center gap-2 font-mono text-[9px] uppercase tracking-[0.14em] text-[var(--ab-dim)]"
              >
                <span className="truncate text-[var(--ab-text)]">
                  {SURVIVAL_SIGNAL_LABEL[k]}
                </span>
                <SignalBar value={v} />
                <span
                  className="text-right tabular-nums"
                  style={{ color: v >= 0 ? "var(--ab-glow)" : "var(--ab-death)" }}
                >
                  {v >= 0 ? "+" : "−"}
                  {Math.abs(v).toFixed(2)}
                </span>
              </li>
            );
          })}
        </ul>
      </div>

      <footer className="font-mono text-[9px] uppercase tracking-[0.2em] text-[var(--ab-dim)]/70">
        market {m.market_id}
      </footer>
    </section>
  );
}

export default SurvivalMatchCard;
