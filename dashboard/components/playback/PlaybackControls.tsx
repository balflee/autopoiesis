"use client";

/**
 * PlaybackControls — keyboard contract for the PLAYBACK takeover.
 *
 * PRD §8 keyboard contract:
 *   - Space          → toggle play / pause
 *   - ArrowLeft      → step back one tick (pauses)
 *   - ArrowRight     → step forward one tick (pauses)
 *   - Escape         → return to LIVE (route navigation, owned by parent)
 *
 * The component itself renders the on-screen affordance hint strip plus
 * the play/pause + prev/next buttons (touch targets ≥44 px per PRD §8).
 * Keyboard listening is wired through `useEffect`, mirroring the existing
 * PlaybackTakeover keyboard contract from sprint_1.
 *
 * The parent owns the navigation side-effect on Escape so unit tests can
 * mock it without dragging in the Next.js router.
 */

import { useEffect } from "react";
import type { JSX } from "react";

export interface PlaybackControlsProps {
  readonly isPlaying: boolean;
  readonly onTogglePlay: () => void;
  readonly onNext: () => void;
  readonly onPrev: () => void;
  readonly onExitToLive: () => void;
  /** When true the keyboard listener is attached. Default true. */
  readonly keyboardEnabled?: boolean;
}

export function PlaybackControls(props: PlaybackControlsProps): JSX.Element {
  const {
    isPlaying,
    onTogglePlay,
    onNext,
    onPrev,
    onExitToLive,
    keyboardEnabled = true,
  } = props;

  useEffect(() => {
    if (!keyboardEnabled) return;
    if (typeof document === "undefined") return;

    const onKey = (e: KeyboardEvent) => {
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
        onTogglePlay();
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        onNext();
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        onPrev();
      } else if (e.key === "Escape") {
        e.preventDefault();
        onExitToLive();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [keyboardEnabled, onTogglePlay, onNext, onPrev, onExitToLive]);

  return (
    <div
      data-testid="playback-controls"
      className="flex flex-wrap items-center justify-between gap-3 font-mono text-xs uppercase tracking-[0.2em] text-genesis-ink-muted"
    >
      <div className="flex items-center gap-2">
        <ControlButton
          testId="playback-prev"
          label="Previous tick"
          onClick={onPrev}
        >
          ←
        </ControlButton>
        <ControlButton
          testId="playback-toggle-play"
          label={isPlaying ? "Pause" : "Play"}
          onClick={onTogglePlay}
          ariaPressed={isPlaying}
        >
          {isPlaying ? "⏸" : "▶"}
        </ControlButton>
        <ControlButton
          testId="playback-next"
          label="Next tick"
          onClick={onNext}
        >
          →
        </ControlButton>
        <ControlButton
          testId="playback-exit"
          label="Return to LIVE"
          onClick={onExitToLive}
        >
          Esc
        </ControlButton>
      </div>
      <span aria-hidden="true">
        space play/pause &nbsp;·&nbsp; ←→ step &nbsp;·&nbsp; esc back to live
      </span>
    </div>
  );
}

function ControlButton(props: {
  readonly testId: string;
  readonly label: string;
  readonly onClick: () => void;
  readonly ariaPressed?: boolean;
  readonly children: React.ReactNode;
}): JSX.Element {
  return (
    <button
      type="button"
      data-testid={props.testId}
      aria-label={props.label}
      aria-pressed={props.ariaPressed}
      onClick={props.onClick}
      className="flex h-11 min-w-[44px] items-center justify-center rounded-md border border-genesis-ink-muted/40 bg-transparent px-3 text-sm text-genesis-ink hover:border-genesis-amber/70 focus:outline-none focus:ring-2 focus:ring-genesis-amber"
    >
      {props.children}
    </button>
  );
}

export default PlaybackControls;
