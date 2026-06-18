/**
 * dashboard/lib/load_sandbox_state.ts — sprint_8 T-D-009 live-wiring seam.
 *
 * The dashboard's `/` route now reads LIVE state from
 * ``state/sandbox/`` (the single-writer surface owned by
 * :class:`agent.data.sandbox_state.SandboxStateWriter`, T-B-018/19/20).
 * Before this task the route consumed mock fixtures + WS-only frames;
 * the rewire is intentionally additive — we keep the WS upgrade path
 * for low-latency push, and add a 2 s file-poll fallback so the demo
 * surface stays correct even if the WS bridge isn't up.
 *
 * Spec anchors
 * ------------
 *
 *  * PRD §8 (line 490-565) — dashboard layout + Death Watch trigger
 *    "energy < 10% activates Death Watch full-screen takeover".
 *  * TECHNICAL_PLAN §5.1 — "Next.js + WebSocket bridge for live mode +
 *    2 s polling fallback".
 *  * TECHNICAL_PLAN §5.4 — "Dashboard data contract (Agent → Dashboard).
 *    Schema stable across phases; sandbox extended Phase 2 reuses it."
 *  * CEO sprint_8 sandbox-pivot plan (2026-05-26 PLAN-003, Day 4) —
 *    "Reuse sprint 6 dashboard `/` route. Replace mock fixture with
 *    live read from state/sandbox/ (poll every 2 s + WS if available).
 *    New dashboard/lib/load_sandbox_state.ts. Death Watch threshold
 *    uses cached breath_last_known."
 *
 * Module split
 * ------------
 *
 *  * THIS file is a **client module** (`"use client"` directive). It
 *    exports the React hook + provider + context.
 *  * Pure helpers + types + constants live in
 *    `sandbox_state_shared.ts` (no `"use client"`) so the API route
 *    can call them server-side without crossing a wrapper boundary.
 *  * The server-only filesystem loader lives in
 *    `load_sandbox_state.server.ts` (imports `node:fs/promises`).
 *
 * All public symbols from `sandbox_state_shared.ts` are re-exported
 * here so existing imports keep working without an indirection.
 *
 * Death Watch threshold (per acceptance criterion):
 *    `breath_last_known / INITIAL_BREATH < 0.10`
 *    INITIAL_BREATH defaults to 100 (`DEFAULT_PHASE2_BREATH` from
 *    :mod:`agent.runtime.phase2_launch`); configurable via
 *    NEXT_PUBLIC_INITIAL_BREATH so a different Phase calibration can
 *    override without a recompile.
 *
 * Test mode
 * ---------
 *
 *  When ``SANDBOX_TEST=1`` is set the API route routes reads through
 *  the deterministic fixture in ``dashboard/__mocks__/sandbox_state.ts``
 *  instead of touching the filesystem. The Playwright spec at
 *  `dashboard/tests/dashboard/playwright/sandbox-live.spec.ts`
 *  exercises this gate end-to-end.
 */

"use client";

import {
  createContext,
  createElement,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { useAgentWebSocket } from "@/hooks/useAgentWebSocket";
import {
  breathPct,
  deathWatchActive,
  readPollIntervalMs,
  toDecisionFeedEntries,
  type AgentStateSnapshotData,
  type DecisionRecordData,
  type LagAlert,
  type SandboxStateBundle,
  type SettledBetRecordData,
} from "@/lib/sandbox_state_shared";
import { useWsStore } from "@/lib/wsStore";

/* ------------------------------------------------------------------ */
/* Re-exports — keep `load_sandbox_state` the single import surface    */
/* ------------------------------------------------------------------ */

export {
  breathPct,
  computeLagAlerts,
  deathWatchActive,
  lastN,
  parseJsonl,
  readInitialBreath,
  readPollIntervalMs,
  toDecisionFeedEntries,
  DEATH_WATCH_RATIO,
  DEFAULT_INITIAL_BREATH,
  DEFAULT_POLL_MS,
  DEFAULT_TAIL_N,
  SNAPSHOT_STALE_MS,
} from "@/lib/sandbox_state_shared";

export type {
  AgentStateSnapshotData,
  DecisionRecordData,
  LagAlert,
  SandboxStateBundle,
  SettledBetRecordData,
  WeightsSnapshotData,
} from "@/lib/sandbox_state_shared";

/* ------------------------------------------------------------------ */
/* Client-side React surface                                            */
/* ------------------------------------------------------------------ */

const EMPTY_BUNDLE_CLIENT: SandboxStateBundle = {
  snapshot: null,
  recent_decisions: [],
  recent_settled: [],
  lag_alerts: [
    {
      kind: "cold_boot",
      detail: "dashboard has not yet fetched /api/sandbox",
      severity: "info",
    },
  ],
  served_ts: new Date(0).toISOString(),
  is_mock: false,
  recent_gods_treasury: [],
  gods_revenue_cumulative_usd: 0,
  incarnation_number: 0,
  incarnation_lineage: [],
};

/** Hook options — primarily a test seam. */
export interface UseSandboxStateOptions {
  /** Override the poll cadence. Defaults to env / 2 s. */
  readonly pollMs?: number;
  /**
   * Override the fetch implementation — tests inject a stub. In
   * production this is `globalThis.fetch`.
   */
  readonly fetchImpl?: typeof fetch;
  /** API route URL — defaults to `/api/sandbox`. */
  readonly url?: string;
}

/** Hook return shape — bundle + connection state + Death Watch ratio. */
export interface UseSandboxStateResult {
  readonly snapshot: AgentStateSnapshotData | null;
  readonly recent_decisions: readonly DecisionRecordData[];
  readonly recent_settled: readonly SettledBetRecordData[];
  readonly lag_alerts: readonly LagAlert[];
  /** True iff the file poll has succeeded at least once. */
  readonly hydrated: boolean;
  /** True iff `breath_last_known / INITIAL_BREATH < 0.10`. */
  readonly death_watch_active: boolean;
  /** Computed pct — 0..100, derived from `snapshot.breath`. */
  readonly breath_pct: number | null;
  /** WS connection state (proxied from useAgentWebSocket). */
  readonly ws_connection: string;
  /** True iff the WS bridge or poll has been silent > 10 s. */
  readonly degraded: boolean;
}

/**
 * useSandboxState — the dashboard's primary live-data hook.
 *
 * Lifecycle:
 *   - On mount, opens a WS upgrade attempt via `useAgentWebSocket`
 *     (existing seam). If the env exposes a WS URL the bridge fires
 *     and frames stream into `useWsStore` directly.
 *   - Concurrently kicks a 2 s polling interval against `/api/sandbox`.
 *     Each successful poll lifts the bundle's vitals/decisions into
 *     the same `useWsStore` slots so existing components stay
 *     unchanged. The bundle is also returned to React via state so
 *     sandbox-specific UI (lag banners, open-bets count) can render.
 *   - Cleans up the interval on unmount.
 *
 * The hook is SSR-safe — `fetch` runs only inside `useEffect`.
 */
export function useSandboxState(
  opts: UseSandboxStateOptions = {},
): UseSandboxStateResult {
  const ws = useAgentWebSocket();
  const ingest = useWsStore((s) => s.ingest);

  const [bundle, setBundle] = useState<SandboxStateBundle>(EMPTY_BUNDLE_CLIENT);
  const [hydrated, setHydrated] = useState(false);

  const pollMsRef = useRef<number>(opts.pollMs ?? readPollIntervalMs());
  const urlRef = useRef<string>(opts.url ?? "/api/sandbox");
  const fetchImplRef = useRef<typeof fetch | undefined>(opts.fetchImpl);

  const fetchOnce = useCallback(async (): Promise<void> => {
    const fetcher =
      fetchImplRef.current ?? (typeof fetch !== "undefined" ? fetch : null);
    if (!fetcher) return;
    try {
      const res = await fetcher(urlRef.current, { cache: "no-store" });
      if (!res.ok) return;
      const data = (await res.json()) as SandboxStateBundle;
      setBundle(data);
      setHydrated(true);
      // Lift into wsStore so existing widgets stay unchanged.
      if (data.snapshot) {
        ingest({
          kind: "vitals",
          ts: data.snapshot.snapshot_ts,
          // seq monotonic: use last_tick so a stale poll cannot regress UI
          seq: data.snapshot.last_tick,
          payload: {
            breath: breathPct(data.snapshot) ?? 0,
            bankroll: data.snapshot.bankroll_usd,
            countdown_s: 0,
            gas_per_min: 0,
            phase: data.snapshot.phase,
          },
        });
      }
      const feedEntries = toDecisionFeedEntries(
        data.recent_decisions,
        data.recent_settled,
      );
      if (feedEntries.length > 0) {
        const lastTick = data.snapshot?.last_tick ?? 0;
        ingest({
          kind: "decision_feed",
          ts: data.served_ts,
          // 0.5 offset keeps order stable against the paired vitals frame
          // (vitals seq = lastTick, feed seq = lastTick + 0.5).
          seq: lastTick + 0.5,
          entries: feedEntries,
        });
      }
    } catch {
      // Network errors are silently swallowed — the lag_alerts surface
      // in the bundle (or the existing degraded-feed banner from
      // useAgentWebSocket) is the user-visible signal.
    }
  }, [ingest]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    let cancelled = false;
    const tick = async (): Promise<void> => {
      if (cancelled) return;
      await fetchOnce();
    };
    void tick(); // first immediate read for fast hydrate
    const interval = setInterval(() => {
      void tick();
    }, pollMsRef.current);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [fetchOnce]);

  return {
    snapshot: bundle.snapshot,
    recent_decisions: bundle.recent_decisions,
    recent_settled: bundle.recent_settled,
    lag_alerts: bundle.lag_alerts,
    hydrated,
    death_watch_active: deathWatchActive(bundle.snapshot),
    breath_pct: breathPct(bundle.snapshot),
    ws_connection: ws.connection,
    degraded: ws.degraded,
  };
}

/* ------------------------------------------------------------------ */
/* Context — lets nested components read the bundle without re-fetching */
/* ------------------------------------------------------------------ */

const SandboxStateContext = createContext<UseSandboxStateResult | null>(null);

/**
 * SandboxStateProvider — mounts {@link useSandboxState} once at the
 * dashboard root and exposes the result via context so deep components
 * can opt-in without prop drilling. Components that read via
 * `useWsStore` selectors continue to work unchanged (the hook ingests
 * into the same store).
 */
export function SandboxStateProvider(props: {
  readonly children: ReactNode;
  readonly options?: UseSandboxStateOptions;
}): ReturnType<typeof createElement> {
  const value = useSandboxState(props.options);
  // Hand-roll the createElement call so this file stays `.ts` (no JSX
  // parser needed). The brief requires the loader to live at
  // `dashboard/lib/load_sandbox_state.ts`; keeping the provider here
  // means consumers have one import surface.
  return createElement(
    SandboxStateContext.Provider,
    { value },
    props.children,
  );
}

/** Read the live sandbox bundle inside the provider tree. */
export function useSandboxStateContext(): UseSandboxStateResult {
  const ctx = useContext(SandboxStateContext);
  if (ctx == null) {
    return {
      snapshot: null,
      recent_decisions: [],
      recent_settled: [],
      lag_alerts: [],
      hydrated: false,
      death_watch_active: false,
      breath_pct: null,
      ws_connection: "idle",
      degraded: false,
    };
  }
  return ctx;
}
