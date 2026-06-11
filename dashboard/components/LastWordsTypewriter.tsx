"use client";

/**
 * LastWordsTypewriter — per-character animation that reveals the Agent's
 * terminal `dieWithLastWords()` message letter-by-letter (PRD §5.1.B,
 * Demo §9 storyboard 4:30 beat).
 *
 * Implementation choices:
 *   - Driven by `requestAnimationFrame` so the main thread is NOT blocked
 *     (acceptance criterion: typewriter must not block during animation).
 *     Each frame advances the visible substring by Δ chars proportional
 *     to elapsed time at the configured CHARS_PER_SECOND rate.
 *   - Honours `prefers-reduced-motion: reduce`: in that case the full
 *     text renders instantly on first paint (no animation, no rAF loop).
 *   - SSR-safe: matches `prefers-reduced-motion` only inside useEffect
 *     so server render is deterministic.
 *   - Stable testid surface for both states ("typing" vs "done") so the
 *     vitest spec can assert without sleeping.
 *
 * Display contract:
 *   - The text occupies a fixed min-height container so growing letters
 *     do NOT shift the surrounding chrome (CLS protection — see the
 *     DeathWatch grid layout for the matching commitment).
 *   - tx_hash, when present, appears below the text as a small mono link.
 */

import { useEffect, useRef, useState, type JSX } from "react";

import { ColorTokens } from "@/lib/colorTokens";

export interface LastWordsTypewriterProps {
  readonly text: string;
  readonly txHash?: string;
  /** Override the animation rate (chars/sec). Defaults to 22. */
  readonly charsPerSecond?: number;
  /** Test seam: force reduced-motion regardless of media query. */
  readonly forceReducedMotion?: boolean;
}

const DEFAULT_CHARS_PER_SECOND = 22;

/** SSR-safe matchMedia probe. Returns false on the server. */
function prefersReducedMotion(): boolean {
  if (typeof window === "undefined") return false;
  if (typeof window.matchMedia !== "function") return false;
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

export function LastWordsTypewriter(
  props: LastWordsTypewriterProps,
): JSX.Element {
  const {
    text,
    txHash,
    charsPerSecond = DEFAULT_CHARS_PER_SECOND,
    forceReducedMotion,
  } = props;

  // Start at 0 chars; mount effect either fast-forwards (reduced motion)
  // or starts the rAF loop. We render the SSR string at length 0 so the
  // container reserves its layout before hydration kicks the animation in.
  const [visibleChars, setVisibleChars] = useState<number>(0);
  const rafRef = useRef<number | null>(null);
  const startTsRef = useRef<number | null>(null);

  useEffect(() => {
    // Reset whenever the text changes (defensive — Track B should not
    // re-emit a different text in the same session).
    setVisibleChars(0);
    startTsRef.current = null;

    const reduced =
      forceReducedMotion ?? prefersReducedMotion();
    if (reduced) {
      setVisibleChars(text.length);
      return;
    }

    if (typeof window === "undefined") return;
    if (typeof window.requestAnimationFrame !== "function") {
      // Defensive: jsdom may not implement rAF. Fall back to instant.
      setVisibleChars(text.length);
      return;
    }

    const charsPerMs = charsPerSecond / 1000;

    const step = (ts: number) => {
      if (startTsRef.current == null) startTsRef.current = ts;
      const elapsed = ts - startTsRef.current;
      const next = Math.min(text.length, Math.floor(elapsed * charsPerMs));
      setVisibleChars(next);
      if (next < text.length) {
        rafRef.current = window.requestAnimationFrame(step);
      } else {
        rafRef.current = null;
      }
    };
    rafRef.current = window.requestAnimationFrame(step);

    return () => {
      if (rafRef.current != null) {
        window.cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    };
  }, [text, charsPerSecond, forceReducedMotion]);

  const visible = text.slice(0, visibleChars);
  const done = visibleChars >= text.length;

  return (
    <section
      data-testid="last-words-typewriter"
      data-state={done ? "done" : "typing"}
      data-visible-chars={visibleChars}
      data-total-chars={text.length}
      aria-live="polite"
      aria-label="The Agent's last words"
      className="flex w-full max-w-3xl flex-col items-center gap-3 text-center"
    >
      <span
        className="font-mono text-[11px] uppercase tracking-[0.3em]"
        style={{ color: ColorTokens.LOSS }}
      >
        Last Words
      </span>
      {/* Fixed min-height container prevents CLS as the text grows. */}
      <p
        data-testid="last-words-text"
        className="min-h-[6rem] whitespace-pre-wrap font-mono text-lg leading-snug text-genesis-ink sm:text-xl"
      >
        {visible}
        <span
          data-testid="last-words-caret"
          aria-hidden
          className="genesis-typewriter-caret ml-0.5 inline-block w-[0.55ch] text-genesis-amber"
          style={{
            color: ColorTokens.AMBER,
            visibility: done ? "hidden" : "visible",
          }}
        >
          ▍
        </span>
      </p>
      {txHash && (
        <a
          data-testid="last-words-tx-hash"
          href={`https://polygonscan.com/tx/${txHash}`}
          target="_blank"
          rel="noopener noreferrer"
          className="font-mono text-[11px] uppercase tracking-[0.2em] text-genesis-ink-muted underline-offset-2 hover:underline"
        >
          tx · {txHash.slice(0, 10)}…{txHash.slice(-6)}
        </a>
      )}
    </section>
  );
}

export default LastWordsTypewriter;
