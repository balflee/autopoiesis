import { act, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import React from "react";

import "../setup";

import { LLMActivationOverlay } from "../../components/LLMActivationOverlay";
import { useWsStore } from "../../lib/wsStore";
import type { LlmActivatedMessage } from "../../lib/types";

/**
 * LLMActivationOverlay — the one-shot guarantee is the brief's
 * acceptance criterion: a replay test pushes `llm_activated` twice and
 * asserts the overlay renders ONCE.
 *
 * Implementation seam: the store's `llmActivatedShown` latch goes
 * true on the first render. A second `llm_activated` frame ingests
 * idempotently (llmActivated stays true). The overlay component does
 * NOT re-render because its useEffect guard checks
 * `llmActivated && !llmActivatedShown`.
 */

describe("LLMActivationOverlay — one-shot guarantee", () => {
  beforeEach(() => {
    useWsStore.getState().reset();
    if (typeof window !== "undefined") {
      window.sessionStorage.clear();
    }
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  const frame = (seq: number, note?: string): LlmActivatedMessage => ({
    kind: "llm_activated",
    ts: `2026-05-21T12:00:0${seq}Z`,
    seq,
    note,
  });

  it("renders nothing until llm_activated fires", () => {
    render(<LLMActivationOverlay />);
    const root = screen.getByTestId("llm-activation-overlay-root");
    expect(root.getAttribute("data-overlay-rendering")).toBe("false");
    expect(root.getAttribute("data-overlay-shown")).toBe("false");
  });

  it("renders the overlay on the first llm_activated and does NOT re-render on replay", async () => {
    render(<LLMActivationOverlay />);

    act(() => {
      useWsStore.getState().ingest(frame(1, "β₁ unfrozen at Phase 2 boundary"));
    });

    expect(screen.getByTestId("llm-activation-overlay-content")).toBeInTheDocument();
    expect(screen.getByTestId("llm-activation-overlay-headline")).toHaveTextContent(
      /sentient engine awakening/i,
    );
    expect(screen.getByTestId("llm-activation-overlay-subline")).toHaveTextContent(
      /language module online/i,
    );
    expect(useWsStore.getState().llmActivatedShown).toBe(true);

    // Advance past the 1500 ms animation — overlay should retract.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1600);
    });
    expect(screen.getByTestId("llm-activation-overlay-root").getAttribute("data-overlay-rendering")).toBe(
      "false",
    );

    // Replay the same frame (simulating WS reconnect resending the
    // event). The store stays latched and the overlay does NOT mount
    // again — the root remains hidden.
    act(() => {
      useWsStore.getState().ingest(frame(2));
    });

    expect(screen.queryByTestId("llm-activation-overlay-content")).toBeNull();
    // Latch still true.
    expect(useWsStore.getState().llmActivatedShown).toBe(true);
  });

  it("persists the latch across remounts via sessionStorage", () => {
    if (typeof window === "undefined") return;
    window.sessionStorage.setItem("genesis:llm-overlay-shown", "1");
    render(<LLMActivationOverlay />);
    act(() => {
      useWsStore.getState().ingest(frame(1));
    });
    // Even though llmActivated is true, the latch hydrated from
    // sessionStorage prevents a re-render.
    expect(screen.queryByTestId("llm-activation-overlay-content")).toBeNull();
  });
});
