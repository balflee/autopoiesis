"use client";

/**
 * LLMActivationOverlay — one-shot 1500 ms full-screen flourish.
 *
 * Fires once when the Agent's β₁ weight unfreezes at the Phase 2
 * boundary (the demo's "sentient layer awakens" beat, PRD §8 / §9).
 *
 * One-shot guarantee:
 *   - The Zustand store holds two latches: `llmActivated` (set true on
 *     every llm_activated frame, idempotent) and `llmActivatedShown`
 *     (set true exactly once by this component when it has rendered).
 *   - When a fresh `llm_activated` frame replays after a reconnect,
 *     `llmActivated` stays true (no-op) and `llmActivatedShown` is
 *     still true, so the overlay does NOT re-render. Demo audience
 *     never sees a flicker.
 *   - sessionStorage also stores the latch so a hard refresh inside
 *     the demo run does not re-trigger. The reset is per-tab.
 *
 * Animation: CSS keyframes only — keeps Framer Motion out of the
 * bundle (~50 kB saved → important for the lighthouse_perf gate).
 *
 * Copy: "Sentient engine awakening — Language module online"
 * (verbatim from the task brief).
 */

import { useEffect, useRef, useState, type JSX } from "react";

import { ColorTokens } from "@/lib/colorTokens";
import { useWsStore } from "@/lib/wsStore";

const SESSION_STORAGE_KEY = "genesis:llm-overlay-shown";
const ANIMATION_DURATION_MS = 1500;
const HEADLINE = "Sentient engine awakening";
const SUBLINE = "Language module online";

export function LLMActivationOverlay(): JSX.Element | null {
  const llmActivated = useWsStore((s) => s.llmActivated);
  const llmActivatedShown = useWsStore((s) => s.llmActivatedShown);
  const llmActivationNote = useWsStore((s) => s.llmActivationNote);
  const markShown = useWsStore((s) => s.markLlmOverlayShown);

  const [renderOverlay, setRenderOverlay] = useState(false);
  // Per-mount "we already fired" guard so a re-run of the activation
  // effect (triggered by the store ticking llmActivatedShown true) does
  // NOT re-enter the fire logic AND its cleanup does not cancel the
  // pending hide timer. The timer ref keeps the cleanup honest on
  // unmount.
  const firedRef = useRef(false);
  const hideTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Hydrate the latch from sessionStorage on mount (SSR-safe).
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      if (window.sessionStorage.getItem(SESSION_STORAGE_KEY) === "1") {
        markShown();
        firedRef.current = true; // hydrate the per-mount guard too
      }
    } catch {
      /* sessionStorage may be blocked — latch lives in store */
    }
  }, [markShown]);

  useEffect(() => {
    if (!llmActivated) return;
    if (firedRef.current) return;
    // If the store latch was already set BEFORE we mounted (e.g.,
    // sessionStorage hydration or a second mount within the same
    // session), do not fire.
    if (llmActivatedShown) {
      firedRef.current = true;
      return;
    }

    firedRef.current = true;
    setRenderOverlay(true);
    try {
      if (typeof window !== "undefined") {
        window.sessionStorage.setItem(SESSION_STORAGE_KEY, "1");
      }
    } catch {
      /* ignore quota/blocked errors */
    }
    markShown();

    hideTimerRef.current = setTimeout(() => {
      setRenderOverlay(false);
    }, ANIMATION_DURATION_MS);
    // NB: deliberately no cleanup here — the timer must NOT be killed
    // when the effect re-runs (which it will, because markShown flips
    // llmActivatedShown true). Cleanup on unmount lives in the second
    // useEffect below so the timer survives in-mount re-renders.
  }, [llmActivated, llmActivatedShown, markShown]);

  useEffect(
    () => () => {
      if (hideTimerRef.current !== null) {
        clearTimeout(hideTimerRef.current);
        hideTimerRef.current = null;
      }
    },
    [],
  );

  if (!renderOverlay) {
    // Keep a hidden DOM hook so tests can assert that the overlay was
    // attempted exactly once via the data attribute on the root.
    return (
      <div
        data-testid="llm-activation-overlay-root"
        data-overlay-shown={llmActivatedShown ? "true" : "false"}
        data-overlay-rendering="false"
        className="hidden"
        aria-hidden="true"
      />
    );
  }

  return (
    <div
      data-testid="llm-activation-overlay-root"
      data-overlay-shown="true"
      data-overlay-rendering="true"
      className="fixed inset-0 z-[60] flex items-center justify-center"
      role="status"
      aria-live="polite"
      aria-label="LLM activation"
      style={{
        backgroundColor: "rgba(11, 20, 38, 0.92)",
        animation: `llm-overlay-fade ${ANIMATION_DURATION_MS}ms ease-out forwards`,
      }}
    >
      <style>{`
        @keyframes llm-overlay-fade {
          0%   { opacity: 0; }
          12%  { opacity: 1; }
          80%  { opacity: 1; }
          100% { opacity: 0; }
        }
        @keyframes llm-overlay-headline {
          0%   { transform: scale(0.92); letter-spacing: 0.05em; }
          60%  { transform: scale(1.00); letter-spacing: 0.18em; }
          100% { transform: scale(1.00); letter-spacing: 0.18em; }
        }
      `}</style>
      <div
        data-testid="llm-activation-overlay-content"
        className="flex flex-col items-center gap-3 px-6 text-center"
      >
        <span
          aria-hidden
          className="inline-block h-1 w-12 rounded-full"
          style={{ backgroundColor: ColorTokens.AMBER }}
        />
        <h1
          data-testid="llm-activation-overlay-headline"
          className="font-mono text-2xl uppercase sm:text-3xl"
          style={{
            color: ColorTokens.AMBER,
            animation: "llm-overlay-headline 900ms ease-out forwards",
          }}
        >
          {HEADLINE}
        </h1>
        <p
          data-testid="llm-activation-overlay-subline"
          className="font-mono text-sm uppercase tracking-[0.3em]"
          style={{ color: ColorTokens.INK }}
        >
          {SUBLINE}
        </p>
        {llmActivationNote && (
          <p
            data-testid="llm-activation-overlay-note"
            className="max-w-md font-mono text-xs text-genesis-ink-muted"
          >
            {llmActivationNote}
          </p>
        )}
      </div>
    </div>
  );
}

export default LLMActivationOverlay;
