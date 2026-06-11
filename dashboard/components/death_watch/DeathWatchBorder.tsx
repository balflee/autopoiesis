"use client";

/**
 * DeathWatchBorder — T-D-007.
 *
 * The "you have entered the danger zone" red border. Distinct from the
 * full-screen DeathWatch takeover (which only mounts at the §9 4:00-5:00
 * climax once a `terminal_lucidity_entered` or `energy_threshold_crossed`
 * frame lands). The border is the GENTLER, EARLIER signal — it appears
 * the instant BREATH dips below the configured threshold (default 10 %
 * per PRD §8) and stays visible until the breath recovers.
 *
 * Visual contract:
 *   - position: fixed, inset-0 — overlays the viewport edges so it
 *     reads at desktop AND at the 375 px mobile demo target.
 *   - pointer-events: none — purely decorative; never blocks input.
 *   - 4 px loss-red ring with 12 px outer glow, pulsing on a 2 s cycle
 *     with opacity oscillating 0.4 → 1.0 → 0.4 (genesis-loss
 *     foundation). Cycle authored as a `@keyframes` in
 *     `death_watch.css` so reduced-motion users get the static ring
 *     without the animation.
 *
 * Acceptance criterion (verbatim from brief):
 *   "DeathWatchBorder renders only when BREATH < 10 % (configurable via
 *    env DEATH_WATCH_THRESHOLD_PCT for testing)"
 *
 * Test seam: `readEnvThreshold()` resolves window → env → 10 default,
 * so Playwright can run threshold-color-transition assertions against
 * a higher breath value without faking the wsStore.
 */

import { useEffect, useState, type JSX } from "react";

import {
  isBorderVisible,
  readEnvThreshold,
} from "@/lib/death_watch_thresholds";
import { useWsStore } from "@/lib/wsStore";

import { CountdownWidget } from "./CountdownWidget";

/**
 * Resolve the active threshold AFTER mount so the SSR render and the
 * client's first commit always agree (both render as the "not yet
 * mounted" hidden div). The window override (or NEXT_PUBLIC env) is
 * only read inside a client useEffect, which avoids the hydration
 * mismatch that otherwise pins the threshold to the SSR-baked default.
 *
 * Returns `null` until mounted — the caller treats null as "render the
 * SSR-equivalent hidden div" so the static HTML matches the first
 * hydrated paint.
 */
function useThreshold(): number | null {
  const [threshold, setThreshold] = useState<number | null>(null);
  useEffect(() => {
    setThreshold(readEnvThreshold());
    const handler = () => setThreshold(readEnvThreshold());
    if (typeof window !== "undefined") {
      window.addEventListener("genesis:threshold-changed", handler);
    }
    return () => {
      if (typeof window !== "undefined") {
        window.removeEventListener("genesis:threshold-changed", handler);
      }
    };
  }, []);
  return threshold;
}

export function DeathWatchBorder(): JSX.Element | null {
  // Subscribe with primitive selectors so React only re-renders the
  // border when the relevant numbers change, not on every WS frame.
  const breath = useWsStore((s) => s.vitals?.breath ?? null);
  const terminalEntered = useWsStore((s) => s.terminalLucidityEntered);
  const threshold = useThreshold();
  // Sticky terminal — once Terminal Lucidity has been entered, keep the
  // border on even if breath recovers (mirrors selectDeathWatchVisible
  // for the takeover surface; PRD §6.10 sticky semantics). On SSR
  // (threshold === null) we never short-circuit to visible so the
  // server output matches the first client paint — eliminates the
  // hydration mismatch that otherwise sticks the data-attrs at SSR
  // defaults.
  const visible =
    threshold !== null &&
    (terminalEntered || isBorderVisible(breath, threshold));

  if (!visible) {
    // Render a hidden hook so Playwright + unit tests can assert the
    // negative case without `queryByTestId` returning null.
    return (
      <div
        data-testid="death-watch-border"
        data-visible="false"
        data-threshold-pct={threshold == null ? "" : String(threshold)}
        data-breath-pct={breath == null ? "" : String(breath)}
        data-terminal-entered={terminalEntered ? "true" : "false"}
        className="hidden"
        aria-hidden="true"
      />
    );
  }

  return (
    <>
      <div
        data-testid="death-watch-border"
        data-visible="true"
        data-threshold-pct={String(threshold)}
        data-breath-pct={breath == null ? "" : breath.toFixed(1)}
        data-terminal-entered={terminalEntered ? "true" : "false"}
        // pointer-events-none so the border NEVER blocks demo clicks.
        // aria-hidden because the live region for the death watch lives
        // on the takeover surface (DeathWatch.tsx) — duplicating the
        // alert here would announce twice on screen readers.
        aria-hidden="true"
        className="genesis-death-watch-border pointer-events-none fixed inset-0 z-[60]"
      />
      {/* CountdownWidget pinned to the upper-right so it reads at the
          demo's 1920×1080 capture viewport. pointer-events-auto so the
          countdown remains focusable for screen-reader announcement,
          while the surrounding border stays click-through. */}
      <div
        data-testid="death-watch-corner"
        className="pointer-events-auto fixed right-4 top-4 z-[61] rounded border border-[#E63946]/40 bg-[#0B1426]/85 backdrop-blur-sm shadow-[0_0_24px_rgba(230,57,70,0.35)]"
      >
        <CountdownWidget />
      </div>
    </>
  );
}

export default DeathWatchBorder;
