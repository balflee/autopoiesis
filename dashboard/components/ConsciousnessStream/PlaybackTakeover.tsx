"use client";

import { useEffect, useMemo } from "react";

import type { MemoryBankSnapshot, MemoryBankTick } from "@/lib/memoryBank";
import { ColorTokens, TICK_PHASE_ACCENT } from "@/lib/colorTokens";

import type { PlaybackController } from "./usePlaybackController";

/**
 * Full-surface PLAYBACK takeover.
 *
 * Per PRD §8 "single-tick takeover —— 整个面板被单个 tick 占据":
 *   - Diary text rendered ≥28px (climax bumps to 32px).
 *   - Phase header tinted per {@link TICK_PHASE_ACCENT}.
 *   - 5-dot progress ribbon mirrors `currentIndex`.
 *   - Keyboard: Space play/pause, ←/→ step, Esc exit to LIVE.
 *
 * No network access, no client-side data fetches. The snapshot prop is
 * the JSON module imported by {@link PHASE2_DAY4_SNAPSHOT}.
 */
export interface PlaybackTakeoverProps {
  readonly snapshot: MemoryBankSnapshot;
  readonly controller: PlaybackController;
}

export function PlaybackTakeover({
  snapshot,
  controller,
}: PlaybackTakeoverProps): JSX.Element {
  const {
    currentTick,
    currentIndex,
    isPlaying,
    totalTicks,
    togglePlay,
    next,
    prev,
    exitToLive,
  } = controller;

  // Keyboard contract — PRD §8: Space / ←→ / Esc.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Ignore keystrokes inside text inputs so future search bars don't
      // hijack the demo.
      const target = e.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable)
      ) {
        return;
      }

      if (e.code === "Space" || e.key === " ") {
        e.preventDefault();
        togglePlay();
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        next();
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        prev();
      } else if (e.key === "Escape") {
        e.preventDefault();
        exitToLive();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [togglePlay, next, prev, exitToLive]);

  const accent = useMemo<string>(() => {
    if (currentTick.phase === "outcome" && currentTick.outcome?.result === "WIN") {
      return ColorTokens.WIN;
    }
    return TICK_PHASE_ACCENT[currentTick.phase];
  }, [currentTick]);

  return (
    <section
      data-testid="playback-takeover"
      role="region"
      aria-label={`PLAYBACK ${snapshot.title} tick ${currentTick.tick}`}
      className="flex h-full min-h-[100dvh] w-full flex-col justify-between px-8 py-10 text-genesis-ink sm:px-16 sm:py-14"
      style={{ backgroundColor: ColorTokens.BG }}
    >
      <header className="flex flex-wrap items-baseline justify-between gap-4">
        <div className="flex items-baseline gap-4">
          <span
            data-testid="playback-phase-badge"
            className="font-mono text-sm uppercase tracking-[0.2em]"
            style={{ color: accent }}
          >
            {labelForPhase(currentTick.phase)}
          </span>
          <span className="font-mono text-sm uppercase tracking-[0.2em] text-genesis-ink-muted">
            tick #{currentTick.tick} · day {snapshot.day_index}
          </span>
        </div>
        <h1 className="font-mono text-sm uppercase tracking-[0.2em] text-genesis-ink-muted">
          {snapshot.title}
        </h1>
      </header>

      <article className="flex flex-1 flex-col justify-center gap-8 py-10">
        <DiaryPara
          text={currentTick.diary}
          emphasis={currentTick.phase === "climax"}
        />
        <TickFacts tick={currentTick} accent={accent} />
      </article>

      <footer className="flex flex-col gap-4">
        <ProgressRibbon current={currentIndex} total={totalTicks} accent={accent} />
        <div className="flex items-center justify-between gap-6 font-mono text-xs uppercase tracking-[0.2em] text-genesis-ink-muted">
          <span data-testid="playback-play-state">
            {isPlaying ? "▶ auto-play" : "⏸ paused"}
          </span>
          <span>
            space play/pause &nbsp;·&nbsp; ←→ step &nbsp;·&nbsp; esc back to live
          </span>
        </div>
      </footer>
    </section>
  );
}

function labelForPhase(p: MemoryBankTick["phase"]): string {
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

function DiaryPara(props: { text: string; emphasis: boolean }): JSX.Element {
  // 28px floor (PRD §8). Climax bumps to 32px to land the beat.
  const sizeClass = props.emphasis ? "text-diary-emphasis" : "text-diary-base";
  return (
    <p
      data-testid="playback-diary"
      data-emphasis={props.emphasis ? "true" : "false"}
      className={`${sizeClass} max-w-4xl font-sans text-genesis-ink`}
    >
      {props.text}
    </p>
  );
}

function TickFacts(props: { tick: MemoryBankTick; accent: string }): JSX.Element {
  const { tick, accent } = props;
  return (
    <dl className="grid grid-cols-2 gap-x-10 gap-y-3 font-mono text-base text-genesis-ink-muted sm:grid-cols-4">
      <Fact label="breath" value={tick.vitals.breath.toLocaleString()} />
      <Fact
        label="bankroll"
        value={`$${tick.vitals.bankroll.toFixed(2)}`}
      />
      <Fact
        label="β₁"
        value={tick.weights.beta.toFixed(2)}
        tone={tick.weights.w_s > 0.75 ? accent : undefined}
      />
      <Fact label="w_r/w_s" value={`${tick.weights.w_r.toFixed(2)} / ${tick.weights.w_s.toFixed(2)}`} />
      {tick.decision ? (
        <Fact
          label="decision"
          value={`${tick.decision.action}${
            tick.decision.side ? ` · ${tick.decision.side}` : ""
          }${
            tick.decision.size_usd !== undefined
              ? ` · $${tick.decision.size_usd}`
              : ""
          }`}
          tone={accent}
          span={2}
        />
      ) : null}
      {tick.outcome ? (
        <Fact
          label="outcome"
          value={`${tick.outcome.result} · $${tick.outcome.pnl_usd.toFixed(0)}${
            tick.outcome.final_score ? ` · ${tick.outcome.final_score}` : ""
          }`}
          tone={
            tick.outcome.result === "WIN"
              ? ColorTokens.WIN
              : tick.outcome.result === "LOSS"
                ? ColorTokens.LOSS
                : ColorTokens.INK_MUTED
          }
          span={2}
        />
      ) : null}
    </dl>
  );
}

function Fact(props: {
  label: string;
  value: string;
  tone?: string;
  span?: 1 | 2;
}): JSX.Element {
  const span = props.span === 2 ? "col-span-2" : "";
  return (
    <div className={`flex flex-col gap-1 ${span}`}>
      <dt className="text-xs uppercase tracking-[0.2em]">{props.label}</dt>
      <dd
        className="font-sans text-lg text-genesis-ink"
        style={props.tone ? { color: props.tone } : undefined}
      >
        {props.value}
      </dd>
    </div>
  );
}

function ProgressRibbon(props: {
  current: number;
  total: number;
  accent: string;
}): JSX.Element {
  return (
    <div
      role="progressbar"
      aria-valuemin={1}
      aria-valuemax={props.total}
      aria-valuenow={props.current + 1}
      className="flex items-center gap-3"
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
            className="block h-2 w-12 rounded-full transition-[background-color] duration-200"
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
