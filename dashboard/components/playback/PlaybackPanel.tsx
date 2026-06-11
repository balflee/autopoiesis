"use client";

/**
 * PlaybackPanel — single-tick takeover for the demo's 1:30 – 2:30 window.
 *
 * Renders ONE tick at a time (PRD §8: "single-tick takeover — 整个面板
 * 被单个 tick 占据"). Auto-play marches through the curated arc using
 * each tick's `dwell_ms`; the dominant signal is highlighted in amber
 * (#FFB703) whenever (max - second_max) > 0.3 (PRD §8 amber rule).
 *
 * Typography contract per PRD §8:
 *   - narrative copy ............ Source Serif Pro 28-32px
 *   - tick header (id/phase/day)   JetBrains Mono 12px
 *   - signal + decision + outcome  Inter 14-16px
 *   - bg ....................... #0B1426  (AAA contrast vs #F5F7FA)
 *
 * Auto-play tolerance ±200 ms per acceptance criterion. The state machine
 * lives entirely in this component; the parent owns the Esc-to-LIVE
 * route-navigation side-effect.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { JSX } from "react";

import { ColorTokens, TICK_PHASE_ACCENT } from "@/lib/colorTokens";
import {
  dominantSignal,
  SIGNAL_KEYS,
  SIGNAL_LABEL,
  type PlaybackFixture,
  type PlaybackTick,
  type PlaybackTickPhase,
  type SignalKey,
} from "@/lib/playback_loader";

import { PlaybackControls } from "./PlaybackControls";

export interface PlaybackPanelProps {
  readonly fixture: PlaybackFixture;
  /** Side-effect for Escape — typically `router.push("/")`. */
  readonly onExitToLive: () => void;
  /** Auto-play on mount? Default true. */
  readonly autoplay?: boolean;
  /**
   * Multiplier on every dwell_ms. 1.0 in production; tests pass smaller
   * (e.g. 0.1) so the full 18 s arc fits in a Playwright timeout.
   */
  readonly dwellScale?: number;
}

export function PlaybackPanel(props: PlaybackPanelProps): JSX.Element {
  const { fixture, onExitToLive, autoplay = true, dwellScale = 1 } = props;

  if (fixture.ticks.length === 0) {
    throw new Error("PlaybackPanel: fixture has no ticks");
  }

  const [currentIndex, setCurrentIndex] = useState<number>(0);
  const [isPlaying, setIsPlaying] = useState<boolean>(autoplay);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const autoplaySyncedRef = useRef<boolean>(false);

  // Sync `autoplay` prop changes that arrive AFTER mount (e.g. Next.js
  // App-Router static prerender → client hydration with `?play=0` search
  // param) exactly once. We do not re-sync on every change so that user
  // Space toggles after mount win over a stale prop.
  useEffect(() => {
    if (autoplaySyncedRef.current) return;
    autoplaySyncedRef.current = true;
    setIsPlaying(autoplay);
  }, [autoplay]);

  const ticks = fixture.ticks;
  const totalTicks = ticks.length;
  const currentTick: PlaybackTick = ticks[currentIndex] ?? ticks[0]!;

  const clearTimer = useCallback(() => {
    if (timeoutRef.current !== null) {
      clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
  }, []);

  // Auto-advance — one setTimeout per tick using the tick's own dwell_ms.
  useEffect(() => {
    clearTimer();
    if (!isPlaying) return;
    if (currentIndex >= totalTicks - 1) return; // halt on final reflection

    const tick = ticks[currentIndex];
    if (!tick) return;
    const dwell = Math.max(0, Math.floor(tick.dwell_ms * dwellScale));
    timeoutRef.current = setTimeout(() => {
      setCurrentIndex((i) => Math.min(i + 1, totalTicks - 1));
    }, dwell);

    return clearTimer;
  }, [isPlaying, currentIndex, ticks, totalTicks, dwellScale, clearTimer]);

  useEffect(() => clearTimer, [clearTimer]);

  const togglePlay = useCallback(() => {
    setIsPlaying((p) => {
      if (p) clearTimer();
      return !p;
    });
  }, [clearTimer]);

  const next = useCallback(() => {
    clearTimer();
    setIsPlaying(false);
    setCurrentIndex((i) => Math.min(i + 1, totalTicks - 1));
  }, [clearTimer, totalTicks]);

  const prev = useCallback(() => {
    clearTimer();
    setIsPlaying(false);
    setCurrentIndex((i) => Math.max(i - 1, 0));
  }, [clearTimer]);

  const handleExit = useCallback(() => {
    clearTimer();
    setIsPlaying(false);
    onExitToLive();
  }, [clearTimer, onExitToLive]);

  const accent = useMemo<string>(() => {
    if (currentTick.phase === "outcome" && currentTick.outcome?.result === "WIN") {
      return ColorTokens.WIN;
    }
    return TICK_PHASE_ACCENT[currentTick.phase];
  }, [currentTick]);

  const dominant = useMemo(
    () => dominantSignal(currentTick.signals),
    [currentTick.signals],
  );

  return (
    <section
      data-testid="playback-panel"
      data-tick-id={currentTick.tick_id}
      data-phase={currentTick.phase}
      data-playing={isPlaying ? "true" : "false"}
      role="region"
      aria-label={`PLAYBACK ${fixture.title} tick ${currentTick.tick_id}`}
      className="flex h-full min-h-[100dvh] w-full flex-col justify-between px-6 py-8 text-genesis-ink sm:px-12 sm:py-12"
      style={{ backgroundColor: ColorTokens.BG }}
    >
      <TickHeader
        fixture={fixture}
        tick={currentTick}
        accent={accent}
        totalTicks={totalTicks}
        currentIndex={currentIndex}
      />

      <NarrativeBlock tick={currentTick} accent={accent} />

      <section className="flex flex-col gap-4">
        <SignalHeatmap
          tick={currentTick}
          dominantKey={dominant.key}
          dominantHighlighted={dominant.highlighted}
        />
        <CardRow tick={currentTick} accent={accent} />
        <ProgressRibbon
          current={currentIndex}
          total={totalTicks}
          accent={accent}
        />
        <PlaybackControls
          isPlaying={isPlaying}
          onTogglePlay={togglePlay}
          onNext={next}
          onPrev={prev}
          onExitToLive={handleExit}
        />
      </section>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* Header strip                                                       */
/* ------------------------------------------------------------------ */

function TickHeader(props: {
  readonly fixture: PlaybackFixture;
  readonly tick: PlaybackTick;
  readonly accent: string;
  readonly totalTicks: number;
  readonly currentIndex: number;
}): JSX.Element {
  const { fixture, tick, accent, totalTicks, currentIndex } = props;
  return (
    <header className="flex flex-wrap items-baseline justify-between gap-3">
      <div className="flex flex-wrap items-baseline gap-3 font-mono text-[12px] uppercase tracking-[0.22em]">
        <span data-testid="playback-tick-id" style={{ color: accent }}>
          tick #{tick.tick_id}
        </span>
        <span
          data-testid="playback-phase-badge"
          className="text-genesis-ink-muted"
        >
          · {labelForPhase(tick.phase)}
        </span>
        <span className="text-genesis-ink-muted">
          · day {tick.day}
        </span>
        <span className="text-genesis-ink-muted">
          · {currentIndex + 1}/{totalTicks}
        </span>
      </div>
      <h1
        data-testid="playback-arc-title"
        className="font-mono text-[12px] uppercase tracking-[0.22em] text-genesis-ink-muted"
      >
        {fixture.title}
      </h1>
    </header>
  );
}

/* ------------------------------------------------------------------ */
/* Narrative block                                                    */
/* ------------------------------------------------------------------ */

function NarrativeBlock(props: {
  readonly tick: PlaybackTick;
  readonly accent: string;
}): JSX.Element {
  const { tick, accent } = props;
  const emphasised = tick.phase === "climax";
  const sizeClass = emphasised ? "text-diary-emphasis" : "text-diary-base";
  return (
    <article className="flex flex-1 flex-col justify-center gap-6 py-8">
      <p
        data-testid="playback-narrative"
        data-emphasis={emphasised ? "true" : "false"}
        className={`${sizeClass} max-w-4xl font-serif-display text-genesis-ink`}
      >
        {tick.narrative}
      </p>
      {tick.reflection ? (
        <blockquote
          data-testid="playback-reflection"
          className="max-w-4xl border-l-2 pl-5 font-serif-display text-diary-base italic text-genesis-ink"
          style={{ borderColor: accent }}
        >
          “{tick.reflection}”
        </blockquote>
      ) : null}
    </article>
  );
}

/* ------------------------------------------------------------------ */
/* Signal heatmap                                                     */
/* ------------------------------------------------------------------ */

function SignalHeatmap(props: {
  readonly tick: PlaybackTick;
  readonly dominantKey: SignalKey;
  readonly dominantHighlighted: boolean;
}): JSX.Element {
  const { tick, dominantKey, dominantHighlighted } = props;
  return (
    <div
      data-testid="playback-signals"
      className="grid grid-cols-5 gap-2 sm:gap-3"
    >
      {SIGNAL_KEYS.map((k) => {
        const value = tick.signals[k];
        const isDominant = k === dominantKey;
        const highlight = isDominant && dominantHighlighted;
        return (
          <div
            key={k}
            data-testid={`playback-signal-${k}`}
            data-dominant={isDominant ? "true" : "false"}
            data-highlighted={highlight ? "true" : "false"}
            className="flex flex-col gap-1 rounded-md border bg-black/30 px-2 py-2"
            style={{
              borderColor: highlight
                ? ColorTokens.AMBER
                : "rgba(159,176,196,0.25)",
            }}
          >
            <div className="flex items-baseline justify-between text-[14px] font-sans">
              <span
                className="font-semibold"
                style={{
                  color: highlight ? ColorTokens.AMBER : ColorTokens.INK,
                }}
              >
                {SIGNAL_LABEL[k]}
              </span>
              <span
                className="font-mono text-[12px]"
                style={{
                  color: highlight ? ColorTokens.AMBER : ColorTokens.INK_MUTED,
                }}
              >
                {value.toFixed(2)}
              </span>
            </div>
            <Bar value={value} highlighted={highlight} />
          </div>
        );
      })}
    </div>
  );
}

function Bar(props: {
  readonly value: number;
  readonly highlighted: boolean;
}): JSX.Element {
  const pct = Math.max(0, Math.min(1, props.value)) * 100;
  const color = props.highlighted ? ColorTokens.AMBER : ColorTokens.INK;
  return (
    <div
      aria-hidden="true"
      className="h-1.5 w-full overflow-hidden rounded-full"
      style={{ backgroundColor: "rgba(159,176,196,0.18)" }}
    >
      <div
        className="h-full rounded-full transition-[width] duration-300"
        style={{ width: `${pct}%`, backgroundColor: color }}
      />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Decision + outcome cards                                           */
/* ------------------------------------------------------------------ */

function CardRow(props: {
  readonly tick: PlaybackTick;
  readonly accent: string;
}): JSX.Element {
  const { tick, accent } = props;
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      <DecisionCard decision={tick.decision} accent={accent} />
      <OutcomeCard outcome={tick.outcome} />
    </div>
  );
}

function DecisionCard(props: {
  readonly decision: PlaybackTick["decision"];
  readonly accent: string;
}): JSX.Element | null {
  const d = props.decision;
  if (!d) {
    return (
      <div
        data-testid="playback-decision-empty"
        className="rounded-md border border-genesis-ink-muted/20 bg-black/20 px-4 py-3 text-[14px] font-sans text-genesis-ink-muted"
      >
        <span className="font-mono text-[12px] uppercase tracking-[0.22em]">
          decision
        </span>
        <div className="mt-1">—</div>
      </div>
    );
  }
  return (
    <div
      data-testid="playback-decision"
      className="rounded-md border bg-black/25 px-4 py-3 font-sans text-[14px] text-genesis-ink sm:text-[16px]"
      style={{ borderColor: props.accent }}
    >
      <div className="font-mono text-[12px] uppercase tracking-[0.22em] text-genesis-ink-muted">
        decision
      </div>
      <div className="mt-1 flex flex-wrap items-baseline gap-3">
        <span data-testid="playback-decision-action" style={{ color: props.accent }}>
          {d.action}
        </span>
        {d.side ? (
          <span data-testid="playback-decision-side">{d.side}</span>
        ) : null}
        {d.amount !== undefined ? (
          <span data-testid="playback-decision-amount">${d.amount}</span>
        ) : null}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 font-mono text-[12px] uppercase tracking-[0.15em] text-genesis-ink-muted">
        {d.score !== undefined ? (
          <span>score {d.score.toFixed(2)}</span>
        ) : null}
        {d.edge !== undefined ? (
          <span>edge {d.edge.toFixed(2)}%</span>
        ) : null}
        {d.rho_eff !== undefined ? (
          <span>ρ_eff {d.rho_eff.toFixed(2)}</span>
        ) : null}
      </div>
    </div>
  );
}

function OutcomeCard(props: {
  readonly outcome: PlaybackTick["outcome"];
}): JSX.Element {
  const o = props.outcome;
  if (!o) {
    return (
      <div
        data-testid="playback-outcome-empty"
        className="rounded-md border border-genesis-ink-muted/20 bg-black/20 px-4 py-3 text-[14px] font-sans text-genesis-ink-muted"
      >
        <span className="font-mono text-[12px] uppercase tracking-[0.22em]">
          outcome
        </span>
        <div className="mt-1">—</div>
      </div>
    );
  }
  const pnlColor =
    o.pnl > 0
      ? ColorTokens.WIN
      : o.pnl < 0
        ? ColorTokens.LOSS
        : ColorTokens.INK;
  return (
    <div
      data-testid="playback-outcome"
      className="rounded-md border bg-black/25 px-4 py-3 font-sans text-[14px] text-genesis-ink sm:text-[16px]"
      style={{
        borderColor:
          o.result === "WIN" ? ColorTokens.WIN : ColorTokens.LOSS,
      }}
    >
      <div className="font-mono text-[12px] uppercase tracking-[0.22em] text-genesis-ink-muted">
        outcome
      </div>
      <div className="mt-1 flex flex-wrap items-baseline gap-3">
        <span
          data-testid="playback-outcome-pnl"
          className="font-semibold text-[20px]"
          style={{ color: pnlColor }}
        >
          {o.pnl >= 0 ? `+$${o.pnl}` : `−$${Math.abs(o.pnl)}`}
        </span>
        {o.result ? (
          <span data-testid="playback-outcome-result">{o.result}</span>
        ) : null}
      </div>
      {o.final_score ? (
        <div className="mt-2 font-mono text-[12px] uppercase tracking-[0.15em] text-genesis-ink-muted">
          {o.final_score}
        </div>
      ) : null}
      <div className="mt-1 font-mono text-[10px] tracking-[0.1em] text-genesis-ink-muted">
        settled {o.settled_at}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Progress ribbon                                                    */
/* ------------------------------------------------------------------ */

function ProgressRibbon(props: {
  readonly current: number;
  readonly total: number;
  readonly accent: string;
}): JSX.Element {
  return (
    <div
      role="progressbar"
      aria-valuemin={1}
      aria-valuemax={props.total}
      aria-valuenow={props.current + 1}
      className="flex items-center gap-2"
      data-testid="playback-progress"
    >
      {Array.from({ length: props.total }, (_, i) => {
        const active = i === props.current;
        const visited = i < props.current;
        const bg = active
          ? props.accent
          : visited
            ? ColorTokens.INK
            : ColorTokens.INK_MUTED;
        return (
          <span
            key={i}
            data-testid={`playback-dot-${i}`}
            data-active={active ? "true" : "false"}
            className="block h-2 w-10 rounded-full transition-[background-color] duration-200 sm:w-12"
            style={{
              backgroundColor: bg,
              opacity: visited && !active ? 0.45 : 1,
            }}
          />
        );
      })}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Helpers                                                            */
/* ------------------------------------------------------------------ */

function labelForPhase(p: PlaybackTickPhase): string {
  switch (p) {
    case "lead_in":
      return "lead-in";
    case "climax":
      return "climax";
    case "outcome":
      return "outcome";
    case "reflection":
      return "reflection";
  }
}

export default PlaybackPanel;
