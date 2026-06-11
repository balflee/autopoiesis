import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import React from "react";

import "../setup";

import { useAgentWebSocket } from "../../hooks/useAgentWebSocket";
import { useWsStore } from "../../lib/wsStore";
import type { WsClientConfig } from "../../lib/ws-client";

/**
 * useAgentWebSocket — reconnect determinism + staleness banner.
 *
 * The brief requires: "Playwright test closes + reopens fake WS" — we
 * cover the equivalent here at the unit level with a fake WebSocket
 * constructor, because Playwright with a real WS would race the
 * Next.js dev server. The Playwright suite verifies the visual surface
 * via the mock-bucket path; this suite proves the reconnect logic.
 */

class MockSocket {
  static OPEN = 1;
  static CLOSED = 3;
  static instances: MockSocket[] = [];
  readyState = 0;
  url: string;
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: ((err: unknown) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    MockSocket.instances.push(this);
  }
  fireOpen(): void {
    this.readyState = MockSocket.OPEN;
    this.onopen?.();
  }
  fireMessage(data: unknown): void {
    this.onmessage?.({
      data: typeof data === "string" ? data : JSON.stringify(data),
    });
  }
  fireClose(): void {
    this.readyState = MockSocket.CLOSED;
    this.onclose?.();
  }
  close(): void {
    this.fireClose();
  }
}

function HookProbe(props: { config: Partial<WsClientConfig>; stalenessMs?: number }): JSX.Element {
  const r = useAgentWebSocket({
    config: props.config,
    stalenessThresholdMs: props.stalenessMs,
  });
  return (
    <div
      data-testid="probe"
      data-connection={r.connection}
      data-degraded={r.degraded ? "true" : "false"}
      data-last-seq={r.lastSeq}
    />
  );
}

describe("useAgentWebSocket", () => {
  beforeEach(() => {
    MockSocket.instances = [];
    useWsStore.getState().reset();
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("ingests a frame through the live WS path", () => {
    const fetchImpl = vi.fn(async () => ({ ok: true, json: async () => [] } as unknown as Response));
    render(
      <HookProbe
        config={{
          url: "ws://localhost/ws",
          pollUrl: "http://localhost/state",
          silenceThresholdMs: 5_000,
          pollIntervalMs: 2_000,
          reconnectBaseMs: 1_000,
          WebSocketImpl: MockSocket as unknown as typeof WebSocket,
          fetchImpl: fetchImpl as unknown as typeof fetch,
        }}
      />,
    );

    const sock = MockSocket.instances[0]!;
    act(() => {
      sock.fireOpen();
      sock.fireMessage({
        kind: "vitals",
        ts: "2026-05-21T12:00:00Z",
        seq: 1,
        payload: {
          breath: 50,
          bankroll: 100,
          countdown_s: 60,
          gas_per_min: 0.1,
          phase: "PHASE_1_INFANCY",
        },
      });
    });

    expect(useWsStore.getState().vitals?.breath).toBe(50);
    expect(screen.getByTestId("probe").getAttribute("data-connection")).toBe("open");
  });

  it("recovers after disconnect + reconnect — frames keep flowing without store reset", async () => {
    const fetchImpl = vi.fn(async () => ({ ok: true, json: async () => [] } as unknown as Response));
    render(
      <HookProbe
        config={{
          url: "ws://localhost/ws",
          pollUrl: "http://localhost/state",
          silenceThresholdMs: 5_000,
          pollIntervalMs: 2_000,
          reconnectBaseMs: 1_000,
          WebSocketImpl: MockSocket as unknown as typeof WebSocket,
          fetchImpl: fetchImpl as unknown as typeof fetch,
        }}
      />,
    );

    const sock1 = MockSocket.instances[0]!;
    act(() => {
      sock1.fireOpen();
      sock1.fireMessage({
        kind: "vitals",
        ts: "2026-05-21T12:00:00Z",
        seq: 5,
        payload: {
          breath: 60,
          bankroll: 100,
          countdown_s: 60,
          gas_per_min: 0.1,
          phase: "PHASE_1_INFANCY",
        },
      });
    });
    expect(useWsStore.getState().lastSeq).toBe(5);
    expect(useWsStore.getState().vitals?.breath).toBe(60);

    // Drop the socket — the inner WsClient reschedules reconnect after
    // 1 s (capped exp-backoff base from the config above).
    act(() => {
      sock1.fireClose();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_100);
    });

    expect(MockSocket.instances.length).toBeGreaterThanOrEqual(2);
    const sock2 = MockSocket.instances[MockSocket.instances.length - 1]!;
    act(() => {
      sock2.fireOpen();
      // Replay seq 5 (the WsClient's dedup latch drops it). Then push
      // seq 6 (should land in the store; vitals updated to breath=42).
      sock2.fireMessage({
        kind: "vitals",
        ts: "2026-05-21T12:00:05Z",
        seq: 5,
        payload: {
          breath: 60,
          bankroll: 100,
          countdown_s: 60,
          gas_per_min: 0.1,
          phase: "PHASE_1_INFANCY",
        },
      });
      sock2.fireMessage({
        kind: "vitals",
        ts: "2026-05-21T12:00:10Z",
        seq: 6,
        payload: {
          breath: 42,
          bankroll: 100,
          countdown_s: 50,
          gas_per_min: 0.1,
          phase: "PHASE_1_INFANCY",
        },
      });
    });

    expect(useWsStore.getState().vitals?.breath).toBe(42);
    expect(useWsStore.getState().lastSeq).toBe(6);
  });

  it("flips degraded=true after staleness threshold and back to false on next frame", async () => {
    const fetchImpl = vi.fn(async () => ({ ok: true, json: async () => [] } as unknown as Response));
    render(
      <HookProbe
        stalenessMs={500} // short threshold so the test runs fast
        config={{
          url: "ws://localhost/ws",
          pollUrl: "http://localhost/state",
          silenceThresholdMs: 5_000,
          pollIntervalMs: 2_000,
          reconnectBaseMs: 1_000,
          WebSocketImpl: MockSocket as unknown as typeof WebSocket,
          fetchImpl: fetchImpl as unknown as typeof fetch,
        }}
      />,
    );

    const sock = MockSocket.instances[0]!;
    act(() => {
      sock.fireOpen();
      sock.fireMessage({
        kind: "thought",
        ts: "2026-05-21T12:00:00Z",
        seq: 1,
        text: "ping",
      });
    });
    expect(screen.getByTestId("probe").getAttribute("data-degraded")).toBe("false");

    // Burn past the 500 ms staleness threshold.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_600);
    });
    expect(screen.getByTestId("probe").getAttribute("data-degraded")).toBe("true");

    // A fresh frame clears the banner.
    act(() => {
      sock.fireMessage({
        kind: "thought",
        ts: "2026-05-21T12:00:10Z",
        seq: 2,
        text: "pong",
      });
    });
    expect(screen.getByTestId("probe").getAttribute("data-degraded")).toBe("false");
  });
});
