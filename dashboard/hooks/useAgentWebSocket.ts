"use client";

/**
 * useAgentWebSocket — React-side bridge between {@link WsClient} and the
 * global {@link useWsStore}. Replaces the bare `WsBootstrap` mount with
 * a hook surface so multiple call-sites (page root + future Storybook
 * harness + integration tests) can subscribe in a uniform shape.
 *
 * What the hook owns end-to-end:
 *
 *   1. Boot one WsClient per mount (idempotent via ref), passing it
 *      env-driven URLs OR optional test-injected `config` overrides.
 *   2. Pipe incoming frames into the store's `ingest` action.
 *   3. Maintain a "stale feed" timestamp — if no frame in 10 seconds
 *      the hook flips `degraded === true` so the DegradedFeedBanner
 *      can render. This is independent from the WsClient internal
 *      silence watchdog (which kicks polling at 5s).
 *   4. Surface deterministic disconnect → reconnect behaviour for
 *      Playwright: when the test fake-WS closes + a new fake replaces
 *      it, this hook MUST resync without page reload. The Zustand
 *      `lastSeq` latch handles dedup; the hook simply restarts the
 *      client when it observes a `closed` transition.
 *
 * The hook is SSR-safe — it returns its `degraded`/`connection` state
 * but only opens sockets inside `useEffect`, which Next.js 14 App
 * Router guarantees never fires on the server.
 *
 * Tests inject mocks two ways:
 *   a) Pass `config.WebSocketImpl` directly (vitest unit tests).
 *   b) Populate `window.__GENESIS_MOCK_WS__` before mount (Playwright).
 */

import { useEffect, useRef, useState } from "react";

import {
  buildWsClientFromEnv,
  WsClient,
  type WsClientConfig,
  type WsConnectionState,
} from "@/lib/ws-client";
import {
  isWsMessage,
  type WsMessage,
} from "@/lib/types";
import { useWsStore } from "@/lib/wsStore";

export interface UseAgentWebSocketOptions {
  /**
   * Optional config overrides — primarily a test seam. If omitted, env
   * URLs drive the client. If env URLs are also missing, the hook
   * stays idle and only the `__GENESIS_MOCK_WS__` injection path
   * surfaces frames.
   */
  readonly config?: Partial<WsClientConfig>;
  /**
   * ms — if no frame is observed for this long, mark the feed degraded.
   * Defaults to 10_000 per PRD §8 ("DegradedFeedBanner on stale > 10s").
   * Lowered in tests to make Playwright fast.
   */
  readonly stalenessThresholdMs?: number;
}

export interface UseAgentWebSocketResult {
  /** Current WS connection state — `idle` until the hook boots. */
  readonly connection: WsConnectionState;
  /** True iff no frame in the last `stalenessThresholdMs` ms. */
  readonly degraded: boolean;
  /** Monotonic seq of the most recent ingested frame (debug aid). */
  readonly lastSeq: number;
}

const DEFAULT_STALENESS_MS = 10_000;

export function useAgentWebSocket(
  opts: UseAgentWebSocketOptions = {},
): UseAgentWebSocketResult {
  const ingest = useWsStore((s) => s.ingest);
  const setConnection = useWsStore((s) => s.setConnection);
  const connection = useWsStore((s) => s.connection);
  const lastSeq = useWsStore((s) => s.lastSeq);

  const [degraded, setDegraded] = useState(false);
  const lastFrameAtRef = useRef<number>(0);
  const clientRef = useRef<WsClient | null>(null);
  const stalenessTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stalenessMs = opts.stalenessThresholdMs ?? DEFAULT_STALENESS_MS;

  useEffect(() => {
    // SSR-safe: nothing below runs server-side.
    if (typeof window === "undefined") return;

    /* --- Path A: window mock injection (Playwright / Storybook) --- */
    const mockBucket = (window as unknown as {
      __GENESIS_MOCK_WS__?: unknown[];
    }).__GENESIS_MOCK_WS__;

    const ingestFrame = (msg: WsMessage) => {
      lastFrameAtRef.current = Date.now();
      setDegraded(false);
      ingest(msg);
    };

    let cleanupMockSubscribe: (() => void) | null = null;

    if (Array.isArray(mockBucket)) {
      const frames: WsMessage[] = [];
      for (const c of mockBucket) {
        if (isWsMessage(c)) frames.push(c);
      }
      (window as unknown as { __GENESIS_MOCK_WS_SEEN__?: number }).__GENESIS_MOCK_WS_SEEN__ =
        frames.length;
      if (frames.length > 0) {
        setConnection("open");
        for (const f of frames) ingestFrame(f);
      }

      /* Also let tests push live frames after mount via a callback.
         When `window.__GENESIS_PUSH_WS__` is invoked, we route the
         frame through the same path the real client uses. */
      const pushFn = (raw: unknown) => {
        if (isWsMessage(raw)) ingestFrame(raw);
      };
      (window as unknown as { __GENESIS_PUSH_WS__?: (m: unknown) => void }).__GENESIS_PUSH_WS__ =
        pushFn;
      cleanupMockSubscribe = () => {
        delete (window as unknown as { __GENESIS_PUSH_WS__?: unknown }).__GENESIS_PUSH_WS__;
      };
    }

    /* --- Path B: real WS client (or test-injected config) --- */
    if (clientRef.current === null) {
      const handlers = {
        onMessage: ingestFrame,
        onConnectionChange: (next: WsConnectionState) => {
          setConnection(next);
          // When the underlying socket reports `closed`, the inner
          // WsClient handles reconnect via its exp-backoff timer.
          // We do NOT recreate the client here — that would double up
          // the reconnect storm. Just reset the staleness clock so a
          // fresh post-reconnect frame can clear the banner cleanly.
          if (next === "open") {
            lastFrameAtRef.current = Date.now();
            setDegraded(false);
          }
        },
      };

      const client = opts.config
        ? new WsClient(
            {
              url: opts.config.url ?? "ws://localhost/ws",
              pollUrl: opts.config.pollUrl ?? "http://localhost/state",
              silenceThresholdMs: opts.config.silenceThresholdMs ?? 5_000,
              pollIntervalMs: opts.config.pollIntervalMs ?? 2_000,
              reconnectBaseMs: opts.config.reconnectBaseMs ?? 1_000,
              WebSocketImpl: opts.config.WebSocketImpl,
              fetchImpl: opts.config.fetchImpl,
              nowFn: opts.config.nowFn,
            },
            handlers,
          )
        : buildWsClientFromEnv(handlers, opts.config ?? {});

      if (client) {
        clientRef.current = client;
        client.start();
      }
    }

    /* --- Staleness watchdog (PRD §8 "DegradedFeedBanner on stale > 10s") --- */
    stalenessTimerRef.current = setInterval(() => {
      if (lastFrameAtRef.current === 0) return; // never seen a frame
      const elapsed = Date.now() - lastFrameAtRef.current;
      setDegraded(elapsed > stalenessMs);
    }, 1_000);

    return () => {
      if (stalenessTimerRef.current !== null) {
        clearInterval(stalenessTimerRef.current);
        stalenessTimerRef.current = null;
      }
      if (clientRef.current !== null) {
        clientRef.current.stop();
        clientRef.current = null;
      }
      if (cleanupMockSubscribe) cleanupMockSubscribe();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // Mount-once — store actions are stable Zustand refs.

  return { connection, degraded, lastSeq };
}

export default useAgentWebSocket;
