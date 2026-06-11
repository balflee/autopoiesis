"use client";

/**
 * AgentControls — header bar for the `/` route.
 *
 * PRD §8 line "Dashboard 是 demo 5min 主战场. 必须 interactive: 启停 agent,
 * 看 reflection 实时流, 跑 backtest workshop." This is the START/STOP
 * surface. The visible bar carries three things:
 *
 *   ┌──────────────────────────────────────────────────────────┐
 *   │ ● status   BREATH 72.3  PHASE_2_APPRENTICE      [start] │
 *   │                                                  [stop] │
 *   └──────────────────────────────────────────────────────────┘
 *
 * Behaviour:
 *
 *   - On mount: pings `/api/agent/status` once, then subscribes to the SSE
 *     stream. Status pill goes through `idle → running → stopped → error`.
 *   - The BREATH ticker updates from the SSE: every decisions / reflections
 *     event carries an embedded `breath_after` field on the producer side
 *     (Track B sandbox loop) and we light up a tiny pulse when it ticks.
 *     If the producer hasn't filled that field, we re-poll /status every
 *     5s as a fallback so the cockpit number is never stale.
 *   - Start/stop buttons call the typed api_client and surface 409 / 4xx
 *     in an inline note next to the button.
 *
 * Offline behaviour:
 *
 *   When the backend is unreachable (status === 0 ApiError), the pill goes
 *   to "offline" and surfaces a retry button. PRD acceptance criterion:
 *   "Dashboard does NOT crash when backend is unreachable."
 */

import { useCallback, useEffect, useMemo, useRef, useState, type JSX } from "react";

import { ColorTokens } from "@/lib/colorTokens";
import {
  ApiError,
  fetchStatus,
  startAgent,
  stopAgent,
  type StatusBody,
} from "@/lib/api_client";
import { useConfigureStore } from "@/lib/configureStore";
import {
  subscribeSse,
  type SseStatus,
  type SseSubscription,
} from "@/lib/sse_subscribe";

/** Pill state machine. */
export type AgentLiveStatus =
  | "idle"
  | "running"
  | "stopped"
  | "error"
  | "offline";

interface PillSpec {
  readonly label: string;
  readonly color: string;
  readonly pulse: boolean;
}

const PILL: Record<AgentLiveStatus, PillSpec> = {
  idle: { label: "idle", color: ColorTokens.INK_MUTED, pulse: false },
  running: { label: "running", color: ColorTokens.WIN, pulse: true },
  stopped: { label: "stopped", color: ColorTokens.AMBER, pulse: false },
  error: { label: "error", color: ColorTokens.LOSS, pulse: false },
  offline: { label: "offline", color: ColorTokens.LOSS, pulse: false },
};

export interface AgentControlsProps {
  /**
   * Inject a fixed status — Storybook + Playwright bypass the live backend
   * by providing this so the screenshot is deterministic. When omitted the
   * component lights up its own data pipe.
   */
  readonly mockStatus?: StatusBody;
  readonly mockLiveStatus?: AgentLiveStatus;
  /** Skip the network entirely — tests use this to bypass fetch on mount. */
  readonly suppressNetwork?: boolean;
}

export function AgentControls(props: AgentControlsProps = {}): JSX.Element {
  const [liveStatus, setLiveStatus] = useState<AgentLiveStatus>(
    props.mockLiveStatus ?? "idle",
  );
  const [snapshot, setSnapshot] = useState<StatusBody | null>(
    props.mockStatus ?? null,
  );
  const [breathTicker, setBreathTicker] = useState<number | null>(
    props.mockStatus?.breath ?? null,
  );
  const [pulse, setPulse] = useState<boolean>(false);
  const [busy, setBusy] = useState<boolean>(false);
  const [actionNote, setActionNote] = useState<string | null>(null);
  const [sseStatus, setSseStatus] = useState<SseStatus>("idle");

  // T-D-015 — 'Awaiting config' pill. Until Track B promotes the
  // `agent_config.json` existence check to /status as `pending_config:
  // bool` (proposed_spec_change in the T-D-015 delivery report), we
  // light up the pill off two signals OR'd together:
  //
  //   1. snapshot.pending_config === true  ← server-side once shipped
  //   2. local Zustand latch set by `configureAgent()` success
  //
  // The pill renders when EITHER is true AND the agent is not running.
  // Pressing START transitions to running; the next /status poll sees
  // running=true and the pill drops off (the agent now owns the config).
  const staged = useConfigureStore((s) => s.staged);
  const clearStaged = useConfigureStore((s) => s.clearStaged);

  const sseRef = useRef<SseSubscription | null>(null);
  const pulseTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const triggerPulse = useCallback(() => {
    setPulse(true);
    if (pulseTimer.current) clearTimeout(pulseTimer.current);
    pulseTimer.current = setTimeout(() => setPulse(false), 700);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const status = await fetchStatus();
      setSnapshot(status);
      setBreathTicker((prev) => (status.breath != null ? status.breath : prev));
      setLiveStatus(status.running ? "running" : "stopped");
      return status;
    } catch (err) {
      if (err instanceof ApiError && err.status === 0) {
        setLiveStatus("offline");
      } else {
        setLiveStatus("error");
      }
      throw err;
    }
  }, []);

  // ---- mount: pull initial status + open SSE ------------------------- //
  useEffect(() => {
    if (props.suppressNetwork) return;
    let cancelled = false;
    void refresh().catch(() => {
      /* swallow — liveStatus already moved to offline / error */
    });

    sseRef.current = subscribeSse(
      {
        onStatusChange: (s) => {
          if (!cancelled) setSseStatus(s);
        },
        onDecision: (ev) => {
          const next = typeof ev.breath_after === "number" ? ev.breath_after : null;
          if (next != null && !cancelled) {
            setBreathTicker(next);
            triggerPulse();
          }
        },
        onReflection: (ev) => {
          const next = typeof ev.breath_after === "number" ? ev.breath_after : null;
          if (next != null && !cancelled) {
            setBreathTicker(next);
            triggerPulse();
          }
        },
      },
      {},
    );

    // Fallback: re-poll /status every 5s so the ticker doesn't stall if the
    // producer hasn't populated breath_after on its decision rows yet.
    const interval = setInterval(() => {
      if (cancelled) return;
      void refresh().catch(() => {
        /* swallow */
      });
    }, 5_000);

    return () => {
      cancelled = true;
      clearInterval(interval);
      if (pulseTimer.current) clearTimeout(pulseTimer.current);
      sseRef.current?.close();
      sseRef.current = null;
    };
  }, [props.suppressNetwork, refresh, triggerPulse]);

  const onStart = useCallback(async () => {
    setBusy(true);
    setActionNote(null);
    try {
      const r = await startAgent();
      setActionNote(`accepted · run ${r.run_id.slice(0, 8)}…`);
      await refresh();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setActionNote("already running");
        await refresh();
      } else if (err instanceof ApiError && err.status === 0) {
        setLiveStatus("offline");
        setActionNote("offline — backend unreachable");
      } else {
        setLiveStatus("error");
        setActionNote(`error · ${(err as Error).message}`);
      }
    } finally {
      setBusy(false);
    }
  }, [refresh]);

  const onStop = useCallback(async () => {
    setBusy(true);
    setActionNote(null);
    try {
      await stopAgent();
      setActionNote("stopped");
      await refresh();
    } catch (err) {
      if (err instanceof ApiError && err.status === 0) {
        setLiveStatus("offline");
        setActionNote("offline — backend unreachable");
      } else {
        setLiveStatus("error");
        setActionNote(`error · ${(err as Error).message}`);
      }
    } finally {
      setBusy(false);
    }
  }, [refresh]);

  const pill = PILL[liveStatus];
  const phaseText = useMemo(() => {
    const p = snapshot?.phase ?? null;
    if (!p) return "—";
    return p.replace(/^PHASE_/, "").replace(/_/g, " · ").toLowerCase();
  }, [snapshot]);

  const breathDisplay = breathTicker != null ? breathTicker.toFixed(1) : "—";
  const runShort = snapshot?.run_id ? snapshot.run_id.slice(0, 8) : null;

  // 'Awaiting config' visible iff (server says pending_config OR local
  // latch is set) AND the agent is NOT currently running. The latch
  // clears when the agent picks the config up on /api/agent/start —
  // see the effect below.
  const showAwaitingConfig =
    !snapshot?.running
    && liveStatus !== "running"
    && (snapshot?.pending_config === true || staged != null);
  const stagedLabel = staged?.starting_weights.label
    ?? (snapshot?.pending_config === true ? "server-staged" : null);

  // When the agent transitions to running AFTER a staged_at timestamp,
  // clear the local latch — the agent has now picked up the config.
  useEffect(() => {
    if (!staged) return;
    if (snapshot?.running && liveStatus === "running") {
      clearStaged();
    }
  }, [snapshot?.running, liveStatus, staged, clearStaged]);

  return (
    <header
      data-testid="agent-controls"
      data-status={liveStatus}
      data-sse={sseStatus}
      role="region"
      aria-label="Agent live controls"
      className="flex w-full flex-col gap-3 rounded-md border border-genesis-ink-muted/30 bg-genesis-bg/80 px-4 py-3 text-genesis-ink shadow-[0_0_0_1px_rgba(159,176,196,0.04)] backdrop-blur sm:flex-row sm:items-center sm:gap-6 sm:px-5"
    >
      {/* status pill */}
      <div className="flex items-center gap-3 sm:flex-1">
        <span
          data-testid="agent-status-pill"
          className="inline-flex items-center gap-2 rounded-full border px-3 py-1 font-mono text-[10px] uppercase tracking-[0.28em]"
          style={{ borderColor: pill.color, color: pill.color }}
        >
          <span
            aria-hidden
            className={`inline-block h-2 w-2 rounded-full ${pill.pulse ? "animate-pulse" : ""}`}
            style={{ backgroundColor: pill.color }}
          />
          {pill.label}
        </span>

        <span
          data-testid="agent-controls-phase"
          className="font-mono text-[10px] uppercase tracking-[0.22em] text-genesis-ink-muted"
        >
          {phaseText}
        </span>

        {runShort ? (
          <span
            data-testid="agent-controls-run-id"
            className="hidden font-mono text-[10px] uppercase tracking-[0.22em] text-genesis-ink-muted/70 sm:inline"
          >
            run · {runShort}
          </span>
        ) : null}

        {showAwaitingConfig ? (
          <span
            data-testid="agent-controls-awaiting-config"
            title={
              staged?.persisted_path
              ?? "agent_config.json staged — next /api/agent/start will pick it up"
            }
            className="inline-flex items-center gap-1.5 rounded-full border border-genesis-amber/60 bg-genesis-amber/[0.08] px-2.5 py-0.5 font-mono text-[10px] uppercase tracking-[0.22em] text-genesis-amber"
          >
            <span aria-hidden className="inline-block h-1.5 w-1.5 rounded-full bg-genesis-amber" />
            awaiting config
            {stagedLabel ? (
              <span className="text-genesis-amber/70">· {stagedLabel}</span>
            ) : null}
          </span>
        ) : null}
      </div>

      {/* BREATH live ticker */}
      <div
        data-testid="breath-ticker"
        data-pulsing={pulse ? "true" : "false"}
        className="flex items-baseline gap-2 font-mono"
      >
        <span className="text-[10px] uppercase tracking-[0.28em] text-genesis-ink-muted">
          breath
        </span>
        <span
          data-testid="breath-ticker-value"
          className={`text-2xl tabular-nums transition-all duration-300 ${pulse ? "text-genesis-win" : "text-genesis-ink"}`}
          style={{
            textShadow: pulse ? "0 0 12px rgba(6, 214, 160, 0.45)" : "none",
          }}
        >
          {breathDisplay}
        </span>
        <span className="text-[10px] uppercase tracking-[0.22em] text-genesis-ink-muted">
          / 100
        </span>
      </div>

      {/* action buttons */}
      <div className="flex items-center gap-2">
        <button
          type="button"
          data-testid="agent-start-button"
          onClick={onStart}
          disabled={busy || liveStatus === "running"}
          className="rounded-sm border border-genesis-win/60 px-3 py-1.5 font-mono text-[10px] uppercase tracking-[0.28em] text-genesis-win transition-colors hover:bg-genesis-win/10 focus:outline-none focus-visible:ring-2 focus-visible:ring-genesis-win/70 disabled:cursor-not-allowed disabled:border-genesis-ink-muted/30 disabled:text-genesis-ink-muted"
        >
          ▸ start
        </button>
        <button
          type="button"
          data-testid="agent-stop-button"
          onClick={onStop}
          disabled={busy || (liveStatus !== "running" && liveStatus !== "error")}
          className="rounded-sm border border-genesis-amber/60 px-3 py-1.5 font-mono text-[10px] uppercase tracking-[0.28em] text-genesis-amber transition-colors hover:bg-genesis-amber/10 focus:outline-none focus-visible:ring-2 focus-visible:ring-genesis-amber/70 disabled:cursor-not-allowed disabled:border-genesis-ink-muted/30 disabled:text-genesis-ink-muted"
        >
          ■ stop
        </button>

        {liveStatus === "offline" ? (
          <button
            type="button"
            data-testid="agent-retry-button"
            onClick={() => {
              setActionNote(null);
              void refresh().catch(() => {});
            }}
            className="rounded-sm border border-genesis-loss/60 px-3 py-1.5 font-mono text-[10px] uppercase tracking-[0.28em] text-genesis-loss transition-colors hover:bg-genesis-loss/10 focus:outline-none focus-visible:ring-2 focus-visible:ring-genesis-loss/70"
          >
            ↻ retry
          </button>
        ) : null}
      </div>

      {actionNote ? (
        <p
          data-testid="agent-controls-note"
          className="basis-full font-mono text-[10px] uppercase tracking-[0.22em] text-genesis-ink-muted sm:basis-auto"
        >
          {actionNote}
        </p>
      ) : null}

      {sseStatus === "auth_blocked" ? (
        <p
          data-testid="agent-controls-sse-banner"
          className="basis-full rounded-sm border border-genesis-amber/40 bg-genesis-amber/[0.06] px-2 py-1 font-mono text-[10px] uppercase tracking-[0.22em] text-genesis-amber"
        >
          live stream unavailable · breath ticker degrades to /status poll ·
          sprint_10 wires EventSource-friendly auth
        </p>
      ) : null}
    </header>
  );
}

export default AgentControls;
