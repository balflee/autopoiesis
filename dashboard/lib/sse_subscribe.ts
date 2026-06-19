/**
 * sse_subscribe.ts — SSE client for Track B's `/api/state/stream` route.
 *
 * Backend (agent/server/main.py `_sse_event_stream`) emits one event per
 * appended JSONL line across three streams:
 *
 *   event: decisions     data: { ...decision_record_v0.2.0 }
 *   event: reflections   data: { ...reflection record  }
 *   event: proposals     data: { ...strategy_proposal_v0.1.0 }
 *
 * EventSource is the obvious choice; it auto-reconnects with the
 * `retry:` field and the browser already handles the wire protocol. But
 * EventSource has TWO holes we need to plug for the dashboard:
 *
 *   1. **It can't send `Authorization` headers** (only cookies survive the
 *      preflight). Backend `agent/server/auth.py:require_bearer_token`
 *      reads ONLY the `Authorization` header today — no query-string
 *      fallback, no cookie fallback. That means:
 *
 *        - With `DASHBOARD_API_TOKEN` set on the backend, every browser
 *          EventSource handshake returns 401 and the stream never opens.
 *        - With no token configured on the backend (local dev), the
 *          stream opens and live data flows.
 *
 *      We DO NOT smuggle the token into the URL (`?token=<jwt>`): URL
 *      tokens leak via Referer headers, proxy access logs, browser
 *      history, and JS console error frames. That is the wrong tradeoff
 *      for a single header that the backend would have to learn to read
 *      anyway. The right fix is a Track B follow-up (sprint_10) that
 *      teaches `require_bearer_token` a same-origin cookie OR an
 *      `EventSource`-friendly auth proxy. See `authBlocked` flag below.
 *
 *      Until that lands, callers (AgentControls / ReflectionFeed /
 *      ProposalReview) surface an honest "live stream unavailable —
 *      Track B sprint_10 update required" banner. The dashboard does
 *      not crash; it just degrades to /api/agent/status polling for
 *      vitals and an empty reflections / proposals feed.
 *
 *   2. EventSource throws no typed error on auth failure — the .onerror
 *      callback gets a bare Event with `readyState === CLOSED`. We wrap
 *      that in our own `SseError` AND surface a synthetic `authBlocked`
 *      status the first time a token is configured but the stream
 *      refuses to open, so consumers can render the explanatory banner.
 *
 * The exported {@link subscribeSse} returns a tear-down handle; the caller
 * (a React effect typically) invokes it on unmount. Reconnection backoff
 * is exponential + jittered, capped at 30 s so the dashboard recovers
 * from a backend restart without hammering.
 */

import type { BacktestResultRow } from "@/lib/api_client";

/* ------------------------------------------------------------------ */
/* Event shapes mirror the producers (Track B owns these schemas)     */
/* ------------------------------------------------------------------ */

/** One decisions.jsonl row — mirrors decision_record.v0.2.0. */
export interface DecisionStreamEvent {
  readonly action: "BET" | "NO_BET";
  readonly ts?: string;
  readonly side?: string;
  readonly size_usd?: number;
  readonly edge_pct?: number;
  readonly kelly_fraction?: number;
  readonly reasoning?: string;
  readonly degraded_mode?: "none" | "desperate";
  // open-ended — Track B can extend
  readonly [key: string]: unknown;
}

/** One reflections.jsonl row. Track B's reflection schema is loose at the
 * moment (no .dev/contracts schema for it yet — sprint_10), so we accept
 * any object with a free-form `narrative` / `summary` / `insight` string. */
export interface ReflectionStreamEvent {
  readonly ts?: string;
  readonly narrative?: string;
  readonly summary?: string;
  readonly insight?: string;
  readonly tick_id?: number | string;
  readonly [key: string]: unknown;
}

/** One proposals.jsonl row — mirrors strategy_proposal_schema.v0.1.0. */
export interface ProposalStreamEvent {
  readonly proposal_id: string;
  readonly ts: string;
  readonly kind: "weight_delta" | "new_signal_idea" | "prompt_tweak" | string;
  readonly rationale: string;
  readonly proposed_change?: Record<string, unknown>;
  readonly expected_impact?: string | null;
  readonly confidence_pct: number;
  readonly requires_human_approval: boolean;
}

/** Vitals tick — a synthetic event we synthesise from `status` polls so the
 * BREATH ticker has a unified subscription interface. NOT emitted by the
 * backend SSE directly. Sprint_10 may move this onto the wire. */
export interface VitalsStreamEvent {
  readonly breath: number;
  readonly phase: string | null;
  readonly running: boolean;
  readonly bankroll?: number;
}

export type SseEventKind = "decisions" | "reflections" | "proposals";

export interface SseHandlers {
  readonly onDecision?: (event: DecisionStreamEvent) => void;
  readonly onReflection?: (event: ReflectionStreamEvent) => void;
  readonly onProposal?: (event: ProposalStreamEvent) => void;
  /** Fires once on first successful connection + on every reconnect. */
  readonly onOpen?: () => void;
  /** Fires whenever the underlying EventSource raises an error. */
  readonly onError?: (err: SseError) => void;
  /** Fires when the live status of the subscription changes. */
  readonly onStatusChange?: (status: SseStatus) => void;
}

export type SseStatus =
  | "idle"
  | "connecting"
  | "open"
  | "reconnecting"
  | "error"
  | "closed"
  /**
   * Synthetic status raised the first time the EventSource fails to open
   * while a bearer token is configured client-side. This is the dashboard's
   * signal to render a "live stream unavailable — sprint_10 backend update
   * required" banner instead of pretending the feed is just "connecting".
   * See module header docstring for the auth-gap rationale.
   */
  | "auth_blocked";

export class SseError extends Error {
  constructor(message: string, public readonly raw: unknown) {
    super(message);
    this.name = "SseError";
  }
}

export interface SseSubscribeOptions {
  /** Override base URL — tests use this. */
  readonly baseUrl?: string;
  /** Override token resolver — tests use this. */
  readonly tokenProvider?: () => string | null;
  /** Pluggable EventSource for tests (e.g. a polyfill or mock). */
  readonly EventSourceImpl?: typeof EventSource;
  /** Initial backoff (ms) before the first reconnect attempt. */
  readonly initialBackoffMs?: number;
  /** Hard cap for the exponential backoff. */
  readonly maxBackoffMs?: number;
}

export interface SseSubscription {
  /** Snapshot of the live wire status. */
  readonly status: () => SseStatus;
  /** Detach the stream + stop reconnecting. Idempotent. */
  readonly close: () => void;
}

/** Pure helper — used by tests so the backoff curve is deterministic. */
export function nextBackoffMs(
  attempt: number,
  initial: number,
  cap: number,
  random: () => number = Math.random,
): number {
  const exp = Math.min(cap, initial * 2 ** attempt);
  // Decorrelated full-jitter (AWS SDK formula): random * exp. Cheap, good
  // enough for a single-tenant dashboard reconnect loop.
  return Math.floor(random() * exp);
}

/**
 * Subscribe to `/api/state/stream`. Returns a handle the caller closes on
 * teardown. Designed to be called from a React effect:
 *
 *     useEffect(() => {
 *       const sub = subscribeSse({
 *         onReflection: (r) => setRows((rows) => [r, ...rows]),
 *         onStatusChange: setStatus,
 *       });
 *       return () => sub.close();
 *     }, []);
 *
 * Will silently no-op on the server (`typeof EventSource === "undefined"`)
 * so a Next.js Server Component import doesn't trip a ReferenceError.
 */
export function subscribeSse(
  handlers: SseHandlers,
  options: SseSubscribeOptions = {},
): SseSubscription {
  const initialBackoff = options.initialBackoffMs ?? 1000;
  const maxBackoff = options.maxBackoffMs ?? 30_000;
  const ES =
    options.EventSourceImpl ??
    (typeof EventSource !== "undefined" ? EventSource : undefined);

  let status: SseStatus = "idle";
  let closed = false;
  let attempt = 0;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let source: EventSource | null = null;

  const setStatus = (next: SseStatus): void => {
    status = next;
    handlers.onStatusChange?.(next);
  };

  const safe = <T>(parser: () => T, name: string): T | null => {
    try {
      return parser();
    } catch (cause) {
      handlers.onError?.(
        new SseError(`failed to parse ${name} payload: ${(cause as Error).message}`, cause),
      );
      return null;
    }
  };

  // Track whether the operator has configured a bearer token but the
  // EventSource never managed to open. We use this to flip status to
  // `auth_blocked` so the dashboard's SSE banner can honestly explain
  // that the backend's `require_bearer_token` has no EventSource-friendly
  // auth path today. See module docstring.
  let tokenWasPresent = false;
  let openedAtLeastOnce = false;

  const connect = (): void => {
    if (closed) return;
    if (!ES) {
      setStatus("closed");
      handlers.onError?.(
        new SseError("EventSource not available in this environment", null),
      );
      return;
    }
    setStatus(attempt === 0 ? "connecting" : "reconnecting");
    // T-D-011 (sprint_10) — default to the same-origin server-side proxy
    // at `/api/proxy`. The proxy injects the bearer token in the Next.js
    // server runtime so the browser bundle never carries it. Direct mode
    // (local-dev only) is opt-in via NEXT_PUBLIC_DASHBOARD_API_URL_OVERRIDE.
    const base = (options.baseUrl ??
      process.env.NEXT_PUBLIC_DASHBOARD_API_URL_OVERRIDE ??
      "/api/proxy").replace(/\/+$/, "");
    const tokenProvider = options.tokenProvider ?? defaultTokenProvider;
    const token = tokenProvider();
    tokenWasPresent = tokenWasPresent || (token != null && token.length > 0);

    // Intentionally do NOT smuggle the token into the URL — URL-tokens
    // leak via Referer headers, proxy logs, browser history, and JS
    // console error frames. EventSource cannot send Authorization
    // headers, so the only honest answer with the current Track B auth
    // (header-only) is: try open without auth, and if the backend
    // requires a token, surface `auth_blocked` so the dashboard renders
    // the explanatory banner. Sprint_10 will land a cookie-based or
    // proxy-based path that EventSource can use.
    const url = `${base}/api/state/stream`;

    try {
      source = new ES(url, { withCredentials: false });
    } catch (cause) {
      handlers.onError?.(
        new SseError(`failed to construct EventSource: ${(cause as Error).message}`, cause),
      );
      scheduleReconnect();
      return;
    }

    source.addEventListener("open", () => {
      attempt = 0; // reset backoff on a clean open
      openedAtLeastOnce = true;
      setStatus("open");
      handlers.onOpen?.();
    });

    source.addEventListener("decisions", (raw) => {
      const parsed = safe(
        () => JSON.parse((raw as MessageEvent).data) as DecisionStreamEvent,
        "decisions",
      );
      if (parsed) handlers.onDecision?.(parsed);
    });

    source.addEventListener("reflections", (raw) => {
      const parsed = safe(
        () => JSON.parse((raw as MessageEvent).data) as ReflectionStreamEvent,
        "reflections",
      );
      if (parsed) handlers.onReflection?.(parsed);
    });

    source.addEventListener("proposals", (raw) => {
      const parsed = safe(
        () => JSON.parse((raw as MessageEvent).data) as ProposalStreamEvent,
        "proposals",
      );
      if (parsed) handlers.onProposal?.(parsed);
    });

    source.addEventListener("error", (evt) => {
      handlers.onError?.(new SseError("SSE stream error", evt));
      // EventSource will auto-reconnect on transient drops; readyState === 2
      // (CLOSED) means the browser gave up. We then resort to our own backoff.
      if (source && source.readyState === EventSource.CLOSED) {
        // Heuristic: if we've never opened AND a token was configured,
        // assume the backend rejected the unauthenticated request and
        // surface `auth_blocked` so the dashboard's banner explains the
        // gap rather than spinning forever on "reconnecting". We tear
        // down the underlying source + cancel any pending reconnect,
        // but we DO NOT call close() — close() would overwrite the
        // status to "closed" and the banner would never appear.
        if (!openedAtLeastOnce && tokenWasPresent) {
          if (reconnectTimer) {
            clearTimeout(reconnectTimer);
            reconnectTimer = null;
          }
          if (source) {
            source.close();
            source = null;
          }
          setStatus("auth_blocked");
          // Mark the subscription as effectively closed so the explicit
          // close() from React unmount is a clean no-op.
          closed = true;
          return;
        }
        scheduleReconnect();
      } else {
        setStatus("error");
      }
    });
  };

  const scheduleReconnect = (): void => {
    if (closed) return;
    if (reconnectTimer) clearTimeout(reconnectTimer);
    const delay = nextBackoffMs(attempt, initialBackoff, maxBackoff);
    attempt += 1;
    setStatus("reconnecting");
    reconnectTimer = setTimeout(() => {
      if (source) {
        source.close();
        source = null;
      }
      connect();
    }, delay);
  };

  const close = (): void => {
    if (closed) {
      // Already torn down (e.g. by the auth_blocked branch above) —
      // preserve whatever terminal status the caller flipped to.
      return;
    }
    closed = true;
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (source) {
      source.close();
      source = null;
    }
    setStatus("closed");
  };

  connect();
  return {
    status: () => status,
    close,
  };
}

// Local-dev DIRECT-backend token source ONLY. In production the SSE stream
// is reached via the same-origin /api/proxy (token injected server-side),
// so this returns null there and that's correct. We do NOT read
// NEXT_PUBLIC_DASHBOARD_API_TOKEN — a NEXT_PUBLIC_* var is build-time-inlined
// into the browser bundle and would leak the token (the footgun T-D-011's
// proxy model closed). localStorage only (the dev-override path).
function defaultTokenProvider(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem("genesis_api_token");
  } catch {
    return null;
  }
}

/** Re-export for callers that fan in via `lib/sse_subscribe`. */
export type { BacktestResultRow };
