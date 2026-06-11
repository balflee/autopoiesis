/**
 * LastWordsTypewriter.test.tsx — T-D-004 acceptance specs.
 *
 * Spec location: the acceptance brief asked for `tests/dashboard/`, but
 * vitest can only resolve `@testing-library/react` from spec files
 * inside the `dashboard/` package tree (see DeathWatch.test.tsx header
 * for the full rationale). The golden fixture stays at the cross-track
 * `tests/dashboard/__fixtures__/` location.
 *
 * Covers:
 *   1. Reduced-motion media query short-circuits the animation —
 *      visibleChars jumps straight to text.length on first effect.
 *   2. Without reduced motion, the typewriter advances over rAF ticks
 *      and finishes on data-state="done" with the full text rendered.
 *   3. tx_hash, when present, surfaces as a polygonscan link with the
 *      truncated 10-char prefix + 6-char suffix.
 *   4. Without tx_hash, no link element is rendered.
 */

import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import React from "react";

import "../setup";

import { LastWordsTypewriter } from "@/components/LastWordsTypewriter";

// Shared golden fixture lives at the repo-root so T-B-010 can import it.
import fixtures from "../../../tests/dashboard/__fixtures__/death_watch_events.json";

const SHORT = "I existed for sixty-three hours.";
const fixtureLong = (fixtures.last_words_emitted_with_tx as { text: string }).text;
const fixtureTxHash = (
  fixtures.last_words_emitted_with_tx as { tx_hash: string }
).tx_hash;

describe("LastWordsTypewriter", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders the full text instantly when forceReducedMotion=true", () => {
    render(<LastWordsTypewriter text={SHORT} forceReducedMotion />);
    const section = screen.getByTestId("last-words-typewriter");
    expect(section.getAttribute("data-state")).toBe("done");
    expect(section.getAttribute("data-visible-chars")).toBe(String(SHORT.length));
    expect(screen.getByTestId("last-words-text").textContent).toContain(SHORT);
  });

  it("advances the visible substring across rAF ticks until done", () => {
    // Fake-timers don't drive requestAnimationFrame natively in jsdom;
    // we stub rAF to a setTimeout shim so vi.advanceTimersByTime steps it.
    const rafs: Array<(t: number) => void> = [];
    const originalRaf = window.requestAnimationFrame;
    const originalCaf = window.cancelAnimationFrame;
    let now = 0;
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => {
      const id = rafs.length + 1;
      rafs.push(cb);
      return id as unknown as number;
    });
    vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => {
      /* no-op for these tests */
    });

    try {
      render(
        <LastWordsTypewriter
          text={SHORT}
          // 200 chars/sec — at ~16ms per frame the visible substring
          // advances ~3 chars per tick; SHORT is 31 chars → ~11 ticks.
          charsPerSecond={200}
        />,
      );

      const section = screen.getByTestId("last-words-typewriter");
      expect(section.getAttribute("data-state")).toBe("typing");

      // Drain the rAF queue, advancing the timestamp each iteration.
      let guard = 0;
      while (rafs.length > 0 && guard++ < 200) {
        const cb = rafs.shift()!;
        now += 17;
        act(() => {
          cb(now);
        });
      }
      expect(guard).toBeLessThan(200); // didn't hang

      const finalSection = screen.getByTestId("last-words-typewriter");
      expect(finalSection.getAttribute("data-state")).toBe("done");
      expect(finalSection.getAttribute("data-visible-chars")).toBe(
        String(SHORT.length),
      );
      expect(screen.getByTestId("last-words-text").textContent).toContain(SHORT);
    } finally {
      window.requestAnimationFrame = originalRaf;
      window.cancelAnimationFrame = originalCaf;
    }
  });

  it("renders the truncated tx_hash link when provided", () => {
    render(
      <LastWordsTypewriter
        text={fixtureLong}
        txHash={fixtureTxHash}
        forceReducedMotion
      />,
    );
    const link = screen.getByTestId("last-words-tx-hash");
    expect(link.tagName).toBe("A");
    expect(link.getAttribute("href")).toBe(
      `https://polygonscan.com/tx/${fixtureTxHash}`,
    );
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toContain("noopener");
    // 10-char prefix + ellipsis + 6-char suffix.
    expect(link.textContent).toContain(fixtureTxHash.slice(0, 10));
    expect(link.textContent).toContain(fixtureTxHash.slice(-6));
  });

  it("omits the tx_hash link when txHash is undefined", () => {
    render(<LastWordsTypewriter text="hello world" forceReducedMotion />);
    expect(screen.queryByTestId("last-words-tx-hash")).toBeNull();
  });
});
