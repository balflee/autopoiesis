"use client";

/**
 * SandboxLiveBootstrap — client wrapper that mounts
 * {@link SandboxStateProvider} for the dashboard root.
 *
 * Distinct from `WsBootstrap` (which owns the WebSocket bridge) because
 * the two seams have different responsibilities:
 *
 *  - WsBootstrap:           opens the WS connection, surfaces the
 *                           DegradedFeedBanner over its children.
 *  - SandboxLiveBootstrap:  mounts the 2 s file-poll hook +
 *                           SandboxStateProvider context so any nested
 *                           component can `useSandboxStateContext()`
 *                           without prop drilling.
 *
 * Both wrappers ingest into the SAME `useWsStore`, so existing widgets
 * (VitalsPanel, DualEngineMeter, ConsciousnessStream, DecisionFeed,
 * Death Watch) continue reading from selectors that already work — the
 * file poll just becomes the second writer when WS is silent.
 *
 * The lag-alert surface is intentionally compact (top-of-page tape, not
 * a full banner) so it does not push the demo's vitals strip down. The
 * DegradedFeedBanner from WsBootstrap remains the primary "feed stale"
 * cue at the page chrome level.
 */

import type { JSX, ReactNode } from "react";

import {
  SandboxStateProvider,
  useSandboxStateContext,
} from "@/lib/load_sandbox_state";

export function SandboxLiveBootstrap(props: {
  readonly children?: ReactNode;
}): JSX.Element {
  return (
    <SandboxStateProvider>
      <SandboxLagTape />
      {props.children}
    </SandboxStateProvider>
  );
}

/**
 * Compact "tape" banner that surfaces the most-severe lag alert above
 * the dashboard chrome. Hidden when there are no alerts.
 *
 * data-testid is provided so the Playwright spec can assert visibility.
 */
function SandboxLagTape(): JSX.Element | null {
  const ctx = useSandboxStateContext();
  if (ctx.lag_alerts.length === 0) return null;
  // Render the most-severe alert first (error > warn > info).
  const severity = (s: string): number =>
    s === "error" ? 2 : s === "warn" ? 1 : 0;
  const sorted = [...ctx.lag_alerts].sort(
    (a, b) => severity(b.severity) - severity(a.severity),
  );
  const top = sorted[0];
  if (top === undefined) return null;
  const tone =
    top.severity === "error"
      ? "border-genesis-loss text-genesis-loss"
      : top.severity === "warn"
        ? "border-genesis-amber text-genesis-amber"
        : "border-genesis-ink-muted text-genesis-ink-muted";
  return (
    <div
      data-testid="sandbox-lag-tape"
      data-kind={top.kind}
      data-severity={top.severity}
      role="status"
      aria-live="polite"
      className={`mx-4 mt-2 rounded border bg-genesis-bg/40 px-3 py-1 font-mono text-xs uppercase tracking-[0.18em] ${tone}`}
    >
      sandbox · {top.kind.replace(/_/g, " ")} · {top.detail}
    </div>
  );
}
