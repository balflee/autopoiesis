"use client";

/**
 * BacktestScrubber — reusable timeline-scrubber primitive.
 *
 * Originally Phase-1-fixture-bound (T-D-008); refactored (E1 / codex M5) to a
 * GENERIC, fixture-agnostic view-model so it can scrub BOTH the Phase-1 epoch
 * timeline AND the survival run's life timeline (lives + deaths) without
 * casting one fixture into the other.
 *
 * The caller supplies `stepCount`, optional segment `boundaries` (fractions in
 * [0,1] — epoch transitions or life starts), optional `deaths` (markers at
 * step indices, rendered in the permadeath color), and the current `label`
 * (e.g. "life 3 / 7"). Dragging publishes the new step index via {@link
 * onChange}; the parent owns the state and lifts derived chart pieces into
 * `useMemo`s so each drag tick is O(1).
 *
 * Survival adapter: `adaptScrubberViewModel` in `lib/load_survival_journey.ts`.
 *
 * Playback (auto-advance) uses `requestAnimationFrame` with an explicit
 * steps/second budget; we never call setState faster than the browser's vsync.
 */

import { useCallback, useEffect, useMemo, useRef, type JSX } from "react";

import { AbyssColors, ColorTokens, type ChartVariant } from "@/lib/colorTokens";

const STEPS_PER_SECOND_DEFAULT = 60;

/** Per-variant scrubber palette (track / thumb / markers / play dot). */
interface ScrubberPalette {
  /** Filled portion of the track + the playing-state dot. */
  readonly fill: string;
  /** Empty portion of the track. */
  readonly trackEmpty: string;
  /** Thumb body + its glow + the paused-state dot. */
  readonly thumb: string;
  /** Thumb border (reads against the surrounding panel). */
  readonly thumbBorder: string;
  /** Cosmetic segment-boundary marks. */
  readonly boundary: string;
}

const SCRUBBER_PALETTES: Record<ChartVariant, ScrubberPalette> = {
  navy: {
    fill: ColorTokens.WIN,
    trackEmpty: `${ColorTokens.INK_MUTED}40`,
    thumb: ColorTokens.AMBER,
    thumbBorder: ColorTokens.BG,
    boundary: ColorTokens.INK_MUTED,
  },
  abyss: {
    // The played track + "playing" dot glow lime; the thumb sits in lime too so
    // the scrubber reads as one bioluminescent control. The empty track + the
    // border are quiet moss/near-black so the abyss floor stays continuous.
    fill: AbyssColors.GLOW,
    trackEmpty: `${AbyssColors.MOSS}55`,
    thumb: AbyssColors.GLOW,
    thumbBorder: AbyssColors.BG,
    boundary: AbyssColors.DIM,
  },
};

/** A death marker on the timeline. */
export interface ScrubberDeathMarker {
  readonly stepIndex: number;
  readonly cause?: string;
}

/** Generic, fixture-agnostic scrubber view-model. */
export interface ScrubberViewModel {
  /** Total number of steps (the slider spans 0..stepCount-1). */
  readonly stepCount: number;
  /** Segment-boundary fractions in [0,1] (epoch/life transitions). */
  readonly boundaries?: readonly number[];
  /** Death markers, drawn in the permadeath color. */
  readonly deaths?: readonly ScrubberDeathMarker[];
  /** Header kicker (e.g. "survival journey"). */
  readonly title?: string;
}

export interface BacktestScrubberProps {
  readonly viewModel: ScrubberViewModel;
  readonly activeIndex: number;
  readonly onChange: (next: number) => void;
  /** Per-step status line shown in the footer (e.g. "life 3 / 7 · breath 812"). */
  readonly statusLabel?: string;
  readonly playing?: boolean;
  readonly onTogglePlay?: () => void;
  /** Steps/second when auto-playing. Defaults to 60. */
  readonly playbackSpeed?: number;
  /**
   * Color theme. Defaults to `"navy"` (the original Phase-1 palette — legacy
   * callers are byte-unchanged). The /survival page passes `"abyss"`.
   */
  readonly variant?: ChartVariant;
}

export function BacktestScrubber({
  viewModel,
  activeIndex,
  onChange,
  statusLabel,
  playing = false,
  onTogglePlay,
  playbackSpeed = STEPS_PER_SECOND_DEFAULT,
  variant = "navy",
}: BacktestScrubberProps): JSX.Element {
  const palette = SCRUBBER_PALETTES[variant];
  const max = Math.max(viewModel.stepCount - 1, 0);
  const current = clamp(activeIndex, 0, max);
  const pct = max > 0 ? (current / max) * 100 : 0;

  /* ── Auto-play loop ─────────────────────────────────────────────── */
  const rafRef = useRef<number | null>(null);
  const lastTsRef = useRef<number | null>(null);
  const stepIdxRef = useRef(current);
  stepIdxRef.current = current;

  useEffect(() => {
    if (!playing) {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
      lastTsRef.current = null;
      return;
    }
    const step = (ts: number) => {
      if (lastTsRef.current === null) lastTsRef.current = ts;
      const dt = ts - lastTsRef.current;
      const advance = Math.floor((dt / 1000) * playbackSpeed);
      if (advance > 0) {
        const next = stepIdxRef.current + advance;
        if (next >= max) {
          onChange(max);
          if (onTogglePlay) onTogglePlay();
          return;
        }
        lastTsRef.current = ts;
        onChange(next);
      }
      rafRef.current = requestAnimationFrame(step);
    };
    rafRef.current = requestAnimationFrame(step);
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
      lastTsRef.current = null;
    };
  }, [playing, playbackSpeed, max, onChange, onTogglePlay]);

  const deathFractions = useMemo(() => {
    if (!viewModel.deaths) return [] as { frac: number; cause?: string }[];
    return viewModel.deaths.map((d) => ({
      frac: max > 0 ? d.stepIndex / max : 0,
      cause: d.cause,
    }));
  }, [viewModel.deaths, max]);

  const handleRangeChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const v = Number(e.target.value);
      if (Number.isFinite(v)) onChange(Math.trunc(v));
    },
    [onChange],
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === " ") {
        e.preventDefault();
        if (onTogglePlay) onTogglePlay();
        return;
      }
      if (e.key === "Home") {
        e.preventDefault();
        onChange(0);
      } else if (e.key === "End") {
        e.preventDefault();
        onChange(max);
      }
    },
    [onChange, onTogglePlay, max],
  );

  return (
    <section
      data-testid="backtest-scrubber"
      data-tick={current}
      role="region"
      aria-label="Timeline scrubber"
      className="flex w-full flex-col gap-3 rounded-xl border border-[var(--ab-moss)]/30 bg-[var(--ab-bg-2)]/60 p-4 text-[var(--ab-text)] sm:p-5"
    >
      <header className="flex flex-wrap items-baseline justify-between gap-3 font-mono text-[11px] uppercase tracking-[0.22em] text-[var(--ab-dim)]">
        <span className="flex items-center gap-3">
          <span>{viewModel.title ?? "journey"}</span>
          <span className="text-[var(--ab-text)]">
            <span data-testid="backtest-scrubber-tick">step {current}</span>
            <span className="text-[var(--ab-dim)]"> / {max}</span>
          </span>
        </span>
        <button
          type="button"
          data-testid="backtest-scrubber-play"
          aria-pressed={playing}
          aria-label={playing ? "Pause playback" : "Play"}
          onClick={onTogglePlay}
          className="inline-flex items-center gap-2 rounded-sm border border-[var(--ab-moss)]/40 px-3 py-1 text-[10px] uppercase tracking-[0.18em] text-[var(--ab-text)] transition-colors hover:border-[var(--ab-glow)] hover:text-[var(--ab-glow)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ab-glow)]/70"
        >
          <span
            aria-hidden
            className="inline-block h-2.5 w-2.5 rounded-full"
            style={{ backgroundColor: playing ? palette.fill : palette.thumb }}
          />
          {playing ? "pause" : "play"}
        </button>
      </header>

      {/* Slider proper */}
      <div className="relative">
        <input
          type="range"
          min={0}
          max={max}
          step={1}
          value={current}
          onChange={handleRangeChange}
          onKeyDown={handleKeyDown}
          aria-label="Scrub through steps"
          aria-valuemin={0}
          aria-valuemax={max}
          aria-valuenow={current}
          data-testid="backtest-scrubber-range"
          className="backtest-scrubber-range relative z-10 block w-full appearance-none bg-transparent focus:outline-none"
          style={
            {
              ["--scrubber-fill" as const]: `${pct.toFixed(2)}%`,
            } as React.CSSProperties
          }
        />
        {/* Segment-boundary marks — purely cosmetic, sit BELOW the input. */}
        <div
          aria-hidden
          className="pointer-events-none absolute inset-x-0 top-1/2 z-0 h-3 -translate-y-1/2"
        >
          {(viewModel.boundaries ?? []).map((p, i) => (
            <span
              key={`b-${i}`}
              data-testid={`scrubber-boundary-${i}`}
              className="absolute top-1/2 h-3 w-px -translate-x-1/2 -translate-y-1/2"
              style={{
                left: `${(p * 100).toFixed(3)}%`,
                backgroundColor: palette.boundary,
                opacity: 0.35,
              }}
            />
          ))}
          {/* Death markers — taller, in the permadeath color. */}
          {deathFractions.map((d, i) => (
            <span
              key={`d-${i}`}
              data-testid={`scrubber-death-${i}`}
              title={d.cause}
              className="absolute top-1/2 h-4 w-[2px] -translate-x-1/2 -translate-y-1/2"
              style={{ left: `${(d.frac * 100).toFixed(3)}%`, backgroundColor: "var(--ab-death)" }}
            />
          ))}
        </div>
      </div>

      {/* Bottom status line */}
      <footer
        data-testid="backtest-scrubber-footer"
        className="flex flex-wrap items-baseline justify-between gap-3 font-mono text-[10px] uppercase tracking-[0.18em] text-[var(--ab-dim)]"
      >
        <span>{statusLabel ?? ""}</span>
        {viewModel.deaths && viewModel.deaths.length > 0 ? (
          <span className="text-[var(--ab-death)]">
            {viewModel.deaths.length} death{viewModel.deaths.length === 1 ? "" : "s"}
          </span>
        ) : null}
      </footer>

      <style jsx>{`
        .backtest-scrubber-range {
          --thumb-size: 18px;
          --track-height: 6px;
          height: var(--thumb-size);
        }
        .backtest-scrubber-range::-webkit-slider-runnable-track {
          height: var(--track-height);
          border-radius: 999px;
          background: linear-gradient(
            to right,
            ${palette.fill} 0%,
            ${palette.fill} var(--scrubber-fill),
            ${palette.trackEmpty} var(--scrubber-fill),
            ${palette.trackEmpty} 100%
          );
        }
        .backtest-scrubber-range::-moz-range-track {
          height: var(--track-height);
          border-radius: 999px;
          background: ${palette.trackEmpty};
        }
        .backtest-scrubber-range::-moz-range-progress {
          height: var(--track-height);
          border-radius: 999px;
          background: ${palette.fill};
        }
        .backtest-scrubber-range::-webkit-slider-thumb {
          -webkit-appearance: none;
          appearance: none;
          width: var(--thumb-size);
          height: var(--thumb-size);
          margin-top: calc((var(--track-height) - var(--thumb-size)) / 2);
          border-radius: 999px;
          background: ${palette.thumb};
          border: 2px solid ${palette.thumbBorder};
          box-shadow: 0 0 0 1px ${palette.thumb}, 0 0 14px ${palette.thumb}66;
          cursor: pointer;
          transition: transform 0.08s ease-out;
        }
        .backtest-scrubber-range::-webkit-slider-thumb:hover,
        .backtest-scrubber-range:focus::-webkit-slider-thumb {
          transform: scale(1.15);
        }
        .backtest-scrubber-range::-moz-range-thumb {
          width: var(--thumb-size);
          height: var(--thumb-size);
          border-radius: 999px;
          background: ${palette.thumb};
          border: 2px solid ${palette.thumbBorder};
          box-shadow: 0 0 0 1px ${palette.thumb}, 0 0 14px ${palette.thumb}66;
          cursor: pointer;
        }
      `}</style>
    </section>
  );
}

function clamp(v: number, lo: number, hi: number): number {
  if (!Number.isFinite(v)) return lo;
  return Math.max(lo, Math.min(hi, Math.trunc(v)));
}

export default BacktestScrubber;
