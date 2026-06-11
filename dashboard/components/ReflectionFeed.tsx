"use client";

/**
 * ReflectionFeed — virtualised scrollable list of recent reflections.
 *
 * Wire source: `/api/state/stream` SSE `event: reflections`. Newest at top.
 * Each row carries:
 *   - timestamp (HH:MM:SS, mono)
 *   - 1-line narrative summary (clipped to ~120 chars)
 *   - on click → expanded card with the full reflection JSON
 *
 * Virtualisation: we keep at most {@link MAX_ROWS} rows in memory. Beyond
 * that we evict from the tail. Browsers don't break at a few hundred DOM
 * nodes, but the demo agent will run for ~12 hours of compressed sim time
 * with reflections every ~2 minutes — we cap to keep things crisp.
 *
 * The brief calls for "virtualised scrollable list"; with our cap +
 * `overflow-y-auto` on a fixed-height container the browser does the
 * heavy lifting. If sprint_10 widens the row count above ~500 we'll
 * swap in react-virtuoso — not a sprint_9 concern.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type JSX,
} from "react";

import {
  subscribeSse,
  type ReflectionStreamEvent,
  type SseStatus,
  type SseSubscription,
} from "@/lib/sse_subscribe";

/** Cap in-memory rows so the DOM stays under control. */
export const MAX_ROWS = 200;

/** A normalised reflection row we render. */
export interface ReflectionRow {
  readonly id: string;
  readonly ts: string;
  readonly narrative: string;
  readonly raw: ReflectionStreamEvent;
}

export interface ReflectionFeedProps {
  /** Storybook / Playwright inject seed rows here. */
  readonly mockRows?: ReadonlyArray<ReflectionRow>;
  /** Skip the SSE subscription — tests use this with mockRows. */
  readonly suppressNetwork?: boolean;
  /** Cap override for tests. */
  readonly maxRows?: number;
}

/** Cheap unique-ish id when the producer doesn't include one. */
function rowIdOf(ev: ReflectionStreamEvent, ix: number): string {
  if (typeof ev.tick_id === "string" || typeof ev.tick_id === "number") {
    return `r-${ev.tick_id}`;
  }
  if (typeof ev.ts === "string") return `r-${ev.ts}-${ix}`;
  return `r-${ix}-${Math.random().toString(36).slice(2, 9)}`;
}

/** Pick the best string field the producer offers. */
function narrativeOf(ev: ReflectionStreamEvent): string {
  if (typeof ev.narrative === "string" && ev.narrative.trim().length > 0) {
    return ev.narrative;
  }
  if (typeof ev.summary === "string" && ev.summary.trim().length > 0) {
    return ev.summary;
  }
  if (typeof ev.insight === "string" && ev.insight.trim().length > 0) {
    return ev.insight;
  }
  return "(reflection — see expanded view)";
}

/** Format ISO ts → HH:MM:SS, gracefully degrading on garbage. */
function formatTs(ts: string | undefined): string {
  if (!ts) return "--:--:--";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return "--:--:--";
  const h = d.getUTCHours().toString().padStart(2, "0");
  const m = d.getUTCMinutes().toString().padStart(2, "0");
  const s = d.getUTCSeconds().toString().padStart(2, "0");
  return `${h}:${m}:${s}`;
}

export function ReflectionFeed(props: ReflectionFeedProps = {}): JSX.Element {
  const cap = props.maxRows ?? MAX_ROWS;
  const [rows, setRows] = useState<ReflectionRow[]>(
    () => (props.mockRows ? Array.from(props.mockRows) : []),
  );
  const [expanded, setExpanded] = useState<string | null>(null);
  const [sseStatus, setSseStatus] = useState<SseStatus>("idle");
  const subRef = useRef<SseSubscription | null>(null);
  const seenSeqRef = useRef<number>(0);

  useEffect(() => {
    if (props.suppressNetwork) return;
    subRef.current = subscribeSse(
      {
        onStatusChange: (s) => setSseStatus(s),
        onReflection: (ev) => {
          seenSeqRef.current += 1;
          const row: ReflectionRow = {
            id: rowIdOf(ev, seenSeqRef.current),
            ts: ev.ts ?? new Date().toISOString(),
            narrative: narrativeOf(ev),
            raw: ev,
          };
          setRows((prev) => {
            // newest at top; cap at MAX_ROWS
            const next = [row, ...prev];
            return next.length > cap ? next.slice(0, cap) : next;
          });
        },
      },
      {},
    );
    return () => {
      subRef.current?.close();
      subRef.current = null;
    };
  }, [props.suppressNetwork, cap]);

  const onToggleExpand = useCallback((id: string) => {
    setExpanded((prev) => (prev === id ? null : id));
  }, []);

  const empty = rows.length === 0;

  const statusBadge = useMemo<{
    label: string;
    color: string;
  }>(() => {
    switch (sseStatus) {
      case "open":
        return { label: "live", color: "text-genesis-win" };
      case "connecting":
      case "reconnecting":
        return { label: "connecting", color: "text-genesis-amber" };
      case "error":
        return { label: "stream error", color: "text-genesis-loss" };
      case "auth_blocked":
        return { label: "auth · sprint_10", color: "text-genesis-amber" };
      case "closed":
        return { label: "closed", color: "text-genesis-ink-muted" };
      default:
        return { label: "idle", color: "text-genesis-ink-muted" };
    }
  }, [sseStatus]);

  return (
    <section
      data-testid="reflection-feed"
      data-empty={empty ? "true" : "false"}
      data-sse-status={sseStatus}
      role="region"
      aria-label="Reflection stream"
      className="flex h-full w-full flex-col gap-3 rounded-md border border-genesis-ink-muted/30 bg-genesis-bg/60 p-4"
    >
      <header className="flex items-baseline justify-between gap-3 border-b border-genesis-ink-muted/15 pb-2">
        <h3 className="font-mono text-[10px] uppercase tracking-[0.28em] text-genesis-ink">
          reflection stream
        </h3>
        <span
          data-testid="reflection-feed-status"
          className={`font-mono text-[10px] uppercase tracking-[0.22em] ${statusBadge.color}`}
        >
          ● {statusBadge.label} · {rows.length}
        </span>
      </header>

      {sseStatus === "auth_blocked" ? (
        <p
          data-testid="reflection-feed-auth-banner"
          role="alert"
          className="rounded-sm border border-genesis-amber/40 bg-genesis-amber/[0.06] px-3 py-2 font-mono text-[10px] uppercase leading-relaxed tracking-[0.22em] text-genesis-amber"
        >
          live stream unavailable — backend bearer token is configured but
          EventSource cannot send <code>Authorization</code> headers.
          sprint_10 ships a cookie / proxy auth path on Track B; until then
          reflections only land after a manual refresh of /api/state.
        </p>
      ) : null}

      <div
        data-testid="reflection-feed-scroll"
        className="relative flex-1 overflow-y-auto pr-1"
        style={{ maxHeight: "320px", minHeight: "180px" }}
      >
        {empty ? (
          <p
            data-testid="reflection-feed-empty"
            className="px-2 py-12 text-center font-mono text-[10px] uppercase tracking-[0.22em] text-genesis-ink-muted/70"
          >
            {props.suppressNetwork
              ? "no reflections — feed offline"
              : sseStatus === "auth_blocked"
              ? "no reflections — live stream blocked (see banner)"
              : "waiting for first reflection…"}
          </p>
        ) : (
          <ol className="flex flex-col gap-1">
            {rows.map((row, idx) => {
              const isExpanded = expanded === row.id;
              return (
                <li
                  key={row.id}
                  data-testid={`reflection-row-${idx}`}
                  data-expanded={isExpanded ? "true" : "false"}
                  className={`group flex flex-col rounded-sm transition-colors ${isExpanded ? "bg-genesis-ink/[0.03]" : "hover:bg-genesis-ink/[0.02]"}`}
                >
                  <button
                    type="button"
                    onClick={() => onToggleExpand(row.id)}
                    className="flex w-full items-baseline gap-3 px-2 py-1.5 text-left focus:outline-none focus-visible:ring-1 focus-visible:ring-genesis-amber/70"
                  >
                    <span
                      data-testid={`reflection-row-${idx}-ts`}
                      className="shrink-0 font-mono text-[10px] uppercase tracking-[0.18em] text-genesis-ink-muted tabular-nums"
                    >
                      {formatTs(row.ts)}
                    </span>
                    <span
                      data-testid={`reflection-row-${idx}-narrative`}
                      className="flex-1 truncate font-serif-display text-[15px] leading-tight text-genesis-ink"
                    >
                      {row.narrative}
                    </span>
                    <span
                      aria-hidden
                      className="shrink-0 font-mono text-[10px] text-genesis-ink-muted/60 transition-transform group-hover:text-genesis-ink-muted"
                    >
                      {isExpanded ? "▾" : "▸"}
                    </span>
                  </button>

                  {isExpanded ? (
                    <pre
                      data-testid={`reflection-row-${idx}-detail`}
                      className="mx-2 mb-2 max-h-48 overflow-auto rounded-sm border border-genesis-ink-muted/15 bg-genesis-bg/80 p-2 font-mono text-[11px] leading-snug text-genesis-ink-muted"
                    >
                      {safeJsonStringify(row.raw)}
                    </pre>
                  ) : null}
                </li>
              );
            })}
          </ol>
        )}
      </div>
    </section>
  );
}

function safeJsonStringify(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export default ReflectionFeed;
