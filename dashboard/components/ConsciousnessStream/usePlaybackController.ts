"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { MemoryBankSnapshot, MemoryBankTick } from "@/lib/memoryBank";

/**
 * View mode for the ConsciousnessStream surface.
 *
 * - `LIVE`     — default; WebSocket feed (stubbed in sprint_1).
 * - `PLAYBACK` — single-tick takeover driven by a curated snapshot.
 */
export type StreamMode = "LIVE" | "PLAYBACK";

export interface PlaybackController {
  readonly mode: StreamMode;
  readonly currentIndex: number;
  readonly currentTick: MemoryBankTick;
  readonly isPlaying: boolean;
  readonly totalTicks: number;
  readonly enterPlayback: () => void;
  readonly exitToLive: () => void;
  readonly play: () => void;
  readonly pause: () => void;
  readonly togglePlay: () => void;
  readonly next: () => void;
  readonly prev: () => void;
  readonly seek: (index: number) => void;
}

export interface PlaybackControllerOptions {
  readonly snapshot: MemoryBankSnapshot;
  /** Initial mode. Defaults to PLAYBACK because that is the demo flow. */
  readonly initialMode?: StreamMode;
  /** Auto-play on mount? Defaults to true. */
  readonly autoplay?: boolean;
  /**
   * Multiplier applied to every dwell_ms (test seam). 1.0 in production,
   * 0 in tests for deterministic step-through.
   */
  readonly dwellScale?: number;
}

/**
 * PLAYBACK state machine.
 *
 * Auto-play schedules a single `setTimeout` per tick using the tick's own
 * `dwell_ms`. The timeout is cleared on pause, seek, prev/next, or unmount.
 * When the final tick's dwell elapses we DO NOT loop — playback stops on
 * the last tick so the reflection beat lands instead of resetting.
 *
 * Keyboard handlers are attached by {@link PlaybackTakeover}; this hook is
 * pure imperative state, no DOM listeners.
 */
export function usePlaybackController(
  opts: PlaybackControllerOptions,
): PlaybackController {
  const {
    snapshot,
    initialMode = "PLAYBACK",
    autoplay = true,
    dwellScale = 1,
  } = opts;

  const ticks = snapshot.ticks;
  if (ticks.length === 0) {
    throw new Error("usePlaybackController: snapshot has no ticks");
  }

  const [mode, setMode] = useState<StreamMode>(initialMode);
  const [currentIndex, setCurrentIndex] = useState<number>(0);
  const [isPlaying, setIsPlaying] = useState<boolean>(autoplay);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearTimer = useCallback(() => {
    if (timeoutRef.current !== null) {
      clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
  }, []);

  // Auto-advance effect — re-runs every time index / play state / mode flips.
  useEffect(() => {
    clearTimer();
    if (mode !== "PLAYBACK" || !isPlaying) return;
    if (currentIndex >= ticks.length - 1) return; // halt on final reflection

    const tick = ticks[currentIndex];
    if (!tick) return;
    const dwell = Math.max(0, Math.floor(tick.dwell_ms * dwellScale));
    timeoutRef.current = setTimeout(() => {
      setCurrentIndex((i) => Math.min(i + 1, ticks.length - 1));
    }, dwell);

    return clearTimer;
  }, [mode, isPlaying, currentIndex, ticks, dwellScale, clearTimer]);

  useEffect(() => clearTimer, [clearTimer]);

  const enterPlayback = useCallback(() => {
    setMode("PLAYBACK");
    setCurrentIndex(0);
    setIsPlaying(true);
  }, []);

  const exitToLive = useCallback(() => {
    clearTimer();
    setIsPlaying(false);
    setMode("LIVE");
  }, [clearTimer]);

  const play = useCallback(() => {
    setIsPlaying(true);
  }, []);

  const pause = useCallback(() => {
    clearTimer();
    setIsPlaying(false);
  }, [clearTimer]);

  const togglePlay = useCallback(() => {
    setIsPlaying((p) => {
      if (p) clearTimer();
      return !p;
    });
  }, [clearTimer]);

  const next = useCallback(() => {
    clearTimer();
    setIsPlaying(false);
    setCurrentIndex((i) => Math.min(i + 1, ticks.length - 1));
  }, [clearTimer, ticks.length]);

  const prev = useCallback(() => {
    clearTimer();
    setIsPlaying(false);
    setCurrentIndex((i) => Math.max(i - 1, 0));
  }, [clearTimer]);

  const seek = useCallback(
    (index: number) => {
      clearTimer();
      setIsPlaying(false);
      const clamped = Math.max(0, Math.min(index, ticks.length - 1));
      setCurrentIndex(clamped);
    },
    [clearTimer, ticks.length],
  );

  const currentTick = ticks[currentIndex] ?? ticks[0]!;

  return useMemo<PlaybackController>(
    () => ({
      mode,
      currentIndex,
      currentTick,
      isPlaying,
      totalTicks: ticks.length,
      enterPlayback,
      exitToLive,
      play,
      pause,
      togglePlay,
      next,
      prev,
      seek,
    }),
    [
      mode,
      currentIndex,
      currentTick,
      isPlaying,
      ticks.length,
      enterPlayback,
      exitToLive,
      play,
      pause,
      togglePlay,
      next,
      prev,
      seek,
    ],
  );
}
