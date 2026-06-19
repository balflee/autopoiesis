import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, afterEach, vi } from "vitest";
import React from "react";

import "../setup";

import { LiveMockBetBadge } from "@/components/LiveMockBetBadge";

/**
 * Landing-page LiveMockBetBadge acceptance suite.
 *
 *   1. A live /api/sandbox bundle → the chip shows "mock-bet live", the agent
 *      state, open-position count, and treasury, linking to /living.
 *   2. No usable backend data (non-2xx) → renders NOTHING (landing stays clean).
 *
 * Stubs global fetch — no real network.
 */
describe("LiveMockBetBadge", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the live chip with state, open count, and treasury", async () => {
    const body = {
      snapshot: { breath: 100, open_bet_ids: ["a", "b", "c"] },
      gods_revenue_cumulative_usd: 80,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, json: async () => body })),
    );

    render(<LiveMockBetBadge />);
    const badge = await screen.findByTestId("live-mockbet-badge");
    expect(badge).toHaveTextContent(/mock-bet live/i);
    expect(badge).toHaveTextContent(/agent ALIVE/);
    expect(badge).toHaveTextContent(/3 open/);
    expect(badge).toHaveTextContent(/treasury \$80/);
    expect(badge.getAttribute("href")).toBe("/living");
  });

  it("renders nothing when the backend has no usable data", async () => {
    const fetchMock = vi.fn(async () => ({ ok: false, json: async () => ({}) }));
    vi.stubGlobal("fetch", fetchMock);

    render(<LiveMockBetBadge />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(screen.queryByTestId("live-mockbet-badge")).toBeNull();
  });
});
