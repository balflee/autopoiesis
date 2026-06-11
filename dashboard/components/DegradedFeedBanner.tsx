"use client";

/**
 * DegradedFeedBanner — surfaces a thin amber strip when the WS feed
 * has been silent for > 10 s (PRD §8 / TP §10 risk 1c).
 *
 * The `useAgentWebSocket` hook owns the staleness clock and exposes
 * `degraded: boolean`. This component is a pure projection — no state
 * of its own.
 */

import type { JSX } from "react";

import { ColorTokens } from "@/lib/colorTokens";

export function DegradedFeedBanner(props: {
  readonly degraded: boolean;
}): JSX.Element | null {
  if (!props.degraded) return null;
  return (
    <aside
      data-testid="degraded-feed-banner"
      role="status"
      aria-live="polite"
      className="sticky top-0 z-20 w-full"
      style={{
        backgroundColor: ColorTokens.AMBER,
        color: ColorTokens.BG,
      }}
    >
      <p className="px-4 py-1 text-center font-mono text-xs uppercase tracking-[0.2em]">
        feed stale &gt; 10s · falling back to polling
      </p>
    </aside>
  );
}

export default DegradedFeedBanner;
