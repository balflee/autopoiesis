/**
 * Typed WebSocket client for the Dashboard.
 *
 * Why this exists: the Agent runtime (Track B) pushes vitals + thought
 * frames over a WebSocket. The Dashboard MUST also be resilient to the
 * WS dropping or never connecting at all — TECHNICAL_PLAN.md §10 risk
 * 1c rates this an "extreme impact" failure mode for the Death Watch
 * demo, because losing the WS for 90 seconds during the death beat
 * would silently freeze the visual that's literally on-screen.
 *
 * Strategy:
 *   1. Try to open the WebSocket.
 *   2. If `open` doesn't fire within 5s, OR if a previously-open socket
 *      hasn't delivered a frame in 5s, start a 2-second HTTP polling
 *      loop against /api/state as a fallback (the "double data source,
 *      take the newer" rule from TP §10).
 *   3. Reconnect with exponential backoff (capped at 30s) when the WS
 *      drops; polling stays alive in the meantime.
 *
 * The store is the OUTPUT seam — components consume via Zustand, so
 * tests can inject mock messages directly into the store without
 * touching the network.
 */

import { isWsMessage, type WsMessage, type WsMessageKind } from "./types";

export interface WsClientConfig {
  readonly url: string;
  /** Polling endpoint used when WS is silent. */
  readonly pollUrl: string;
  /** ms — how long WS silence triggers the polling fallback. */
  readonly silenceThresholdMs: number;
  /** ms — polling interval. TP §10 risk 1c specifies 2000. */
  readonly pollIntervalMs: number;
  /** ms — reconnect base delay (capped at 30 000). */
  readonly reconnectBaseMs: number;
  /** Test seam: alternative WebSocket constructor (e.g. mock-socket). */
  readonly WebSocketImpl?: typeof WebSocket;
  /** Test seam: replaces global `fetch` for polling. */
  readonly fetchImpl?: typeof fetch;
  /** Optional: override the silence-clock now() (test seam). */
  readonly nowFn?: () => number;
}

export interface WsClientHandlers {
  readonly onMessage: (msg: WsMessage) => void;
  readonly onConnectionChange: (
    next: WsConnectionState,
    reason?: string,
  ) => void;
}

export type WsConnectionState =
  | "idle"
  | "connecting"
  | "open"
  | "polling_fallback"
  | "closed";

export const DEFAULT_WS_CONFIG: Omit<WsClientConfig, "url" | "pollUrl"> = {
  silenceThresholdMs: 5_000,
  pollIntervalMs: 2_000,
  reconnectBaseMs: 1_000,
};

/* ------------------------------------------------------------------ */
/* The client                                                          */
/* ------------------------------------------------------------------ */

export class WsClient {
  private socket: WebSocket | null = null;
  private state: WsConnectionState = "idle";
  private lastFrameAt: number = 0;
  private silenceTimer: ReturnType<typeof setInterval> | null = null;
  private pollTimer: ReturnType<typeof setInterval> | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectAttempts = 0;
  private stopped = false;
  private lastSeq = -1;
  private readonly now: () => number;

  constructor(
    private readonly cfg: WsClientConfig,
    private readonly handlers: WsClientHandlers,
  ) {
    this.now = cfg.nowFn ?? (() => Date.now());
  }

  /** Boot the client — opens WS, starts silence watch. */
  start(): void {
    if (this.stopped) {
      throw new Error("WsClient: cannot start a stopped client");
    }
    this.openSocket();
    this.startSilenceWatch();
  }

  /** Tear down everything. Safe to call multiple times. */
  stop(): void {
    this.stopped = true;
    this.clearSilenceTimer();
    this.clearPollTimer();
    this.clearReconnectTimer();
    if (this.socket) {
      try {
        this.socket.close();
      } catch {
        /* ignore — socket may already be in CLOSING */
      }
      this.socket = null;
    }
    this.transition("closed");
  }

  /** Inspect — read only, useful for tests. */
  getState(): WsConnectionState {
    return this.state;
  }

  /* ----- internals ----- */

  private openSocket(): void {
    if (this.stopped) return;
    this.clearReconnectTimer();
    this.transition("connecting");

    const Impl = this.cfg.WebSocketImpl ?? WebSocket;
    let sock: WebSocket;
    try {
      sock = new Impl(this.cfg.url);
    } catch (err) {
      // Some environments (e.g. SSR) throw on `new WebSocket(...)`. We
      // do NOT crash — we fall straight to polling.
      this.handlers.onConnectionChange(
        "polling_fallback",
        `socket-constructor-failed: ${String(err)}`,
      );
      this.transition("polling_fallback");
      this.startPolling();
      return;
    }
    this.socket = sock;

    sock.onopen = () => {
      this.reconnectAttempts = 0;
      this.lastFrameAt = this.now();
      this.transition("open");
      // If we were polling because WS was down, stop now that it's back.
      this.clearPollTimer();
    };

    sock.onmessage = (ev: MessageEvent) => {
      this.lastFrameAt = this.now();
      // WS came back to life — drop the fallback poller.
      if (this.state === "polling_fallback") {
        this.clearPollTimer();
        this.transition("open");
      }
      this.handleFrame(ev.data);
    };

    sock.onerror = () => {
      // Errors fire just before close — let onclose drive the reconnect.
    };

    sock.onclose = () => {
      this.socket = null;
      if (this.stopped) return;
      // Don't immediately hide the failure — surface polling and
      // schedule a reconnect.
      this.startPolling();
      this.scheduleReconnect();
    };
  }

  private handleFrame(raw: unknown): void {
    let parsed: unknown;
    try {
      parsed = typeof raw === "string" ? JSON.parse(raw) : raw;
    } catch {
      return; // unparseable garbage — drop silently
    }
    if (!isWsMessage(parsed)) return;
    // Dedup by seq — polling and WS may emit overlap during failover.
    if (parsed.seq <= this.lastSeq) return;
    this.lastSeq = parsed.seq;
    this.handlers.onMessage(parsed);
  }

  private startSilenceWatch(): void {
    this.clearSilenceTimer();
    this.silenceTimer = setInterval(() => {
      if (this.stopped) return;
      // If we never even saw `open` fire, anchor the silence clock to
      // start-time so the watchdog still triggers polling.
      const anchor = this.lastFrameAt === 0 ? 0 : this.lastFrameAt;
      const elapsed = this.now() - anchor;
      if (this.state === "polling_fallback") return; // already there
      if (elapsed >= this.cfg.silenceThresholdMs) {
        this.startPolling();
      }
    }, 1_000);
  }

  private async pollOnce(): Promise<void> {
    if (this.stopped) return;
    const fetchImpl = this.cfg.fetchImpl ?? fetch;
    try {
      const res = await fetchImpl(this.cfg.pollUrl, {
        cache: "no-store",
      });
      if (!res.ok) return;
      const body = (await res.json()) as unknown;
      // Polling endpoint returns either a single WsMessage or an array
      // of them (recent backlog). Either is fine.
      if (Array.isArray(body)) {
        for (const item of body) this.handleFrame(item);
      } else {
        this.handleFrame(body);
      }
    } catch {
      // Network blip — keep ticking. We'd rather retry than blow up.
    }
  }

  private startPolling(): void {
    if (this.pollTimer !== null) return;
    // Always transition — surfacing the fallback in the UI badge is
    // the whole point. If a fresh WS frame later arrives, onmessage
    // demotes us back to "open" and clears the polling loop.
    this.transition("polling_fallback");
    // Fire one immediately so the dashboard catches up.
    void this.pollOnce();
    this.pollTimer = setInterval(() => {
      void this.pollOnce();
    }, this.cfg.pollIntervalMs);
  }

  private scheduleReconnect(): void {
    if (this.stopped) return;
    this.clearReconnectTimer();
    // Capped exponential backoff: 1s, 2s, 4s, 8s, 16s, 30s, 30s, ...
    const delay = Math.min(
      this.cfg.reconnectBaseMs * 2 ** Math.min(this.reconnectAttempts, 5),
      30_000,
    );
    this.reconnectAttempts += 1;
    this.reconnectTimer = setTimeout(() => this.openSocket(), delay);
  }

  private transition(next: WsConnectionState, reason?: string): void {
    if (next === this.state) return;
    this.state = next;
    this.handlers.onConnectionChange(next, reason);
  }

  private clearSilenceTimer(): void {
    if (this.silenceTimer !== null) {
      clearInterval(this.silenceTimer);
      this.silenceTimer = null;
    }
  }

  private clearPollTimer(): void {
    if (this.pollTimer !== null) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
  }

  private clearReconnectTimer(): void {
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }
}

/**
 * Factory — pulls URLs from env at runtime so tests can swap them in.
 * Returns `null` if NEXT_PUBLIC_WS_URL is unset; callers should render
 * the "WS not yet connected" loading state.
 */
export function buildWsClientFromEnv(
  handlers: WsClientHandlers,
  overrides: Partial<WsClientConfig> = {},
): WsClient | null {
  const url = process.env.NEXT_PUBLIC_WS_URL;
  const pollUrl = process.env.NEXT_PUBLIC_STATE_POLL_URL;
  if (!url || !pollUrl) return null;
  return new WsClient(
    {
      ...DEFAULT_WS_CONFIG,
      url,
      pollUrl,
      ...overrides,
    },
    handlers,
  );
}

export type { WsMessage, WsMessageKind };
