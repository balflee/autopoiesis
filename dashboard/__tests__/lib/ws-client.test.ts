import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";

import "../setup";

import {
  WsClient,
  type WsConnectionState,
} from "../../lib/ws-client";
import { isWsMessage } from "../../lib/types";

/**
 * T-D-002 WS client acceptance suite — covers TP §10 risk 1c.
 *
 * The 2-second polling fallback is the headline test: when the
 * WebSocket has been silent for 5+ seconds, the client MUST start
 * polling at the configured interval and surface frames via the
 * same onMessage handler. Failure here would silently freeze the
 * Death Watch demo.
 */

/** Lightweight mock WebSocket — fires open/message/close on demand. */
class MockSocket {
  static OPEN = 1;
  static CLOSED = 3;
  readyState = 0;
  url: string;
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: ((err: unknown) => void) | null = null;

  static instances: MockSocket[] = [];

  constructor(url: string) {
    this.url = url;
    MockSocket.instances.push(this);
  }

  fireOpen(): void {
    this.readyState = MockSocket.OPEN;
    this.onopen?.();
  }

  fireMessage(data: unknown): void {
    this.onmessage?.({ data: typeof data === "string" ? data : JSON.stringify(data) });
  }

  fireClose(): void {
    this.readyState = MockSocket.CLOSED;
    this.onclose?.();
  }

  close(): void {
    this.fireClose();
  }
}

function buildClient(overrides: {
  fetchImpl?: typeof fetch;
  onMessage?: (m: unknown) => void;
  onConnectionChange?: (s: WsConnectionState) => void;
  silenceMs?: number;
  pollMs?: number;
} = {}): {
  client: WsClient;
  messages: unknown[];
  states: WsConnectionState[];
} {
  const messages: unknown[] = [];
  const states: WsConnectionState[] = [];
  const client = new WsClient(
    {
      url: "ws://localhost:8081/ws",
      pollUrl: "http://localhost:8081/state",
      silenceThresholdMs: overrides.silenceMs ?? 5_000,
      pollIntervalMs: overrides.pollMs ?? 2_000,
      reconnectBaseMs: 1_000,
      WebSocketImpl: MockSocket as unknown as typeof WebSocket,
      fetchImpl: overrides.fetchImpl,
    },
    {
      onMessage: (m) => {
        messages.push(m);
        overrides.onMessage?.(m);
      },
      onConnectionChange: (s) => {
        states.push(s);
        overrides.onConnectionChange?.(s);
      },
    },
  );
  return { client, messages, states };
}

describe("WsClient — happy path", () => {
  beforeEach(() => {
    MockSocket.instances = [];
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("emits vitals frames to onMessage after the socket opens", () => {
    const { client, messages, states } = buildClient();
    client.start();
    const sock = MockSocket.instances[0];
    expect(sock).toBeDefined();
    sock!.fireOpen();
    expect(states).toContain("open");

    const frame = {
      kind: "vitals",
      ts: "2026-05-21T12:00:00Z",
      seq: 1,
      payload: {
        breath: 50,
        bankroll: 100,
        countdown_s: 90,
        gas_per_min: 0.1,
        phase: "PHASE_1_INFANCY",
      },
    };
    sock!.fireMessage(JSON.stringify(frame));
    expect(messages).toHaveLength(1);
    expect((messages[0] as { kind: string }).kind).toBe("vitals");
    client.stop();
  });

  it("dedups frames whose seq is not greater than the highest seen", () => {
    const { client, messages } = buildClient();
    client.start();
    const sock = MockSocket.instances[0]!;
    sock.fireOpen();
    const make = (seq: number) => ({
      kind: "thought" as const,
      ts: "2026-05-21T12:00:00Z",
      seq,
      text: `t${seq}`,
    });
    sock.fireMessage(JSON.stringify(make(1)));
    sock.fireMessage(JSON.stringify(make(2)));
    sock.fireMessage(JSON.stringify(make(2))); // duplicate
    sock.fireMessage(JSON.stringify(make(1))); // out-of-order replay
    expect(messages).toHaveLength(2);
    client.stop();
  });

  it("drops malformed frames silently", () => {
    const { client, messages } = buildClient();
    client.start();
    const sock = MockSocket.instances[0]!;
    sock.fireOpen();
    sock.fireMessage("not json{{");
    sock.fireMessage(JSON.stringify({ kind: "unknown_kind", ts: "x", seq: 5 }));
    expect(messages).toHaveLength(0);
    client.stop();
  });
});

describe("WsClient — polling fallback (TP §10 risk 1c)", () => {
  beforeEach(() => {
    MockSocket.instances = [];
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("kicks in polling after 5 s of WS silence and the polled frames reach onMessage", async () => {
    const polledFrame = {
      kind: "vitals" as const,
      ts: "2026-05-21T12:00:05Z",
      seq: 7,
      payload: {
        breath: 42,
        bankroll: 120,
        countdown_s: 60,
        gas_per_min: 0.1,
        phase: "PHASE_2_APPRENTICE" as const,
      },
    };
    let fetchCount = 0;
    const fetchImpl = vi.fn(async () => {
      fetchCount += 1;
      return {
        ok: true,
        json: async () => polledFrame,
      } as unknown as Response;
    });
    const { client, messages, states } = buildClient({
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    client.start();
    const sock = MockSocket.instances[0]!;
    sock.fireOpen(); // socket opened but never sends a frame

    // Burn 5.1 seconds of silence — the watchdog ticks each 1 s.
    await vi.advanceTimersByTimeAsync(5_100);
    expect(states).toContain("polling_fallback");
    // Initial fetch fires immediately on polling start.
    expect(fetchCount).toBeGreaterThanOrEqual(1);
    // Resolve any pending then-callbacks.
    await vi.advanceTimersByTimeAsync(1);
    expect(messages.some((m) => (m as { seq: number }).seq === 7)).toBe(true);

    // Advance another 2 s — polling cadence should fire again.
    const beforeSecond = fetchCount;
    await vi.advanceTimersByTimeAsync(2_100);
    expect(fetchCount).toBeGreaterThan(beforeSecond);

    client.stop();
  });

  it("starts polling when the WS never opens (constructor throws)", async () => {
    const fetchImpl = vi.fn(async () => ({
      ok: true,
      json: async () => [],
    } as unknown as Response));
    const messages: unknown[] = [];
    const states: WsConnectionState[] = [];
    const ThrowingSocket = class {
      constructor() {
        throw new Error("simulated SSR / no-websocket env");
      }
    } as unknown as typeof WebSocket;
    const client = new WsClient(
      {
        url: "ws://localhost/ws",
        pollUrl: "http://localhost/state",
        silenceThresholdMs: 5_000,
        pollIntervalMs: 2_000,
        reconnectBaseMs: 1_000,
        WebSocketImpl: ThrowingSocket,
        fetchImpl: fetchImpl as unknown as typeof fetch,
      },
      {
        onMessage: (m) => messages.push(m),
        onConnectionChange: (s) => states.push(s),
      },
    );
    client.start();
    expect(states).toContain("polling_fallback");
    expect(fetchImpl).toHaveBeenCalled();
    client.stop();
  });
});

describe("isWsMessage type guard", () => {
  it("accepts all 10 TP §5.4 kinds", () => {
    const samples = [
      { kind: "vitals", ts: "x", seq: 1, payload: {} },
      { kind: "thought", ts: "x", seq: 1, text: "" },
      { kind: "decision", ts: "x", seq: 1, payload: { action: "BET" } },
      { kind: "reflection", ts: "x", seq: 1, insight: "" },
      { kind: "weights_updated", ts: "x", seq: 1, weights: {} },
      { kind: "llm_activated", ts: "x", seq: 1 },
      { kind: "desperate_mode_entered", ts: "x", seq: 1 },
      { kind: "terminal_lucidity_start", ts: "x", seq: 1 },
      { kind: "last_words", ts: "x", seq: 1, text: "" },
      { kind: "death", ts: "x", seq: 1, cause: "ENERGY_DEPLETED" },
    ];
    for (const s of samples) {
      expect(isWsMessage(s)).toBe(true);
    }
  });

  it("rejects unknown kinds and missing required fields", () => {
    expect(isWsMessage(null)).toBe(false);
    expect(isWsMessage({ kind: "fake", ts: "x", seq: 1 })).toBe(false);
    expect(isWsMessage({ kind: "vitals", seq: 1 })).toBe(false); // missing ts
    expect(isWsMessage({ kind: "vitals", ts: "x" })).toBe(false); // missing seq
  });
});
