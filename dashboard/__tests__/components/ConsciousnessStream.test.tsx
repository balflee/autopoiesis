import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import React from "react";

import "../setup";

import { LiveStream } from "../../components/ConsciousnessStream/LiveStream";
import { useWsStore } from "../../lib/wsStore";
import type { ThoughtMessage } from "../../lib/types";

/**
 * T-D-002 LiveStream (consciousness typewriter) acceptance suite.
 *
 * We test LiveStream directly rather than ConsciousnessStream because
 * the outer wrapper boots in PLAYBACK mode (Phase 2 Day 4 demo arc) —
 * the playback test suite already exercises that path. LIVE typewriter
 * behaviour is the T-D-002 increment.
 */

const thoughtFrame = (
  seq: number,
  text: string,
): ThoughtMessage => ({
  kind: "thought",
  ts: `2026-05-21T12:00:${seq.toString().padStart(2, "0")}Z`,
  seq,
  text,
});

describe("LiveStream — typewriter + loading state", () => {
  beforeEach(() => {
    useWsStore.getState().reset();
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders the empty state when no thought frames have been ingested", () => {
    render(<LiveStream />);
    expect(screen.getByTestId("consciousness-empty")).toHaveTextContent(
      /awaiting first thought/i,
    );
  });

  it("typewriter-animates the newest thought one character at a time", () => {
    render(<LiveStream charIntervalMs={10} />);

    act(() => {
      useWsStore.getState().ingest(thoughtFrame(1, "Hello"));
    });
    // Immediately after ingest the newest paragraph mounts but shows
    // zero characters yet (the typewriter effect kicks in on the
    // first interval tick).
    expect(screen.getByTestId("consciousness-newest")).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(10);
    });
    expect(screen.getByTestId("consciousness-newest")).toHaveTextContent(/^H/);

    act(() => {
      vi.advanceTimersByTime(40);
    });
    expect(screen.getByTestId("consciousness-newest")).toHaveTextContent(/^Hello/);
  });

  it("moves earlier thoughts into the dimmer tail when a new frame arrives", () => {
    render(<LiveStream charIntervalMs={0} />);

    act(() => {
      useWsStore.getState().ingest(thoughtFrame(1, "first thought"));
    });
    expect(screen.getByTestId("consciousness-newest")).toHaveTextContent("first thought");

    act(() => {
      useWsStore.getState().ingest(thoughtFrame(2, "second thought"));
    });
    expect(screen.getByTestId("consciousness-newest")).toHaveTextContent("second thought");
    // First entry now lives in the tail.
    expect(screen.getByTestId("consciousness-tail-1")).toHaveTextContent("first thought");
  });

  it("flips the LLM-engaged flag when llm_activated has fired", () => {
    render(<LiveStream charIntervalMs={0} />);
    expect(screen.queryByTestId("consciousness-llm-flag")).toBeNull();
    act(() => {
      useWsStore.getState().ingest({
        kind: "llm_activated",
        ts: "2026-05-21T12:01:00Z",
        seq: 9,
      });
    });
    expect(screen.getByTestId("consciousness-llm-flag")).toBeInTheDocument();
  });
});
