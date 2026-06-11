import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, beforeEach } from "vitest";
import React from "react";

import "../setup";

import { DecisionFeed } from "../../components/DecisionFeed";
import { useWsStore } from "../../lib/wsStore";
import type { DecisionFeedMessage } from "../../lib/types";

/**
 * DecisionFeed acceptance suite.
 *
 *   1. Loading state when no decision_feed frame has landed
 *   2. Row rendering + result colour from a fresh feed payload
 *   3. Click-to-expand shows reasoning + reflection panel
 *   4. Merge-by-id: settling PENDING → WIN updates same row in place
 *   5. Newest-first sort
 */

describe("DecisionFeed", () => {
  beforeEach(() => {
    useWsStore.getState().reset();
  });

  it("shows the loading skeleton when no feed frame has arrived", () => {
    render(<DecisionFeed />);
    const root = screen.getByTestId("decision-feed");
    expect(root.getAttribute("data-loading")).toBe("true");
    expect(root).toHaveTextContent(/waiting for decision_feed frame/i);
  });

  it("renders rows with action + side + size + result", () => {
    render(<DecisionFeed />);
    const frame: DecisionFeedMessage = {
      kind: "decision_feed",
      ts: "2026-05-21T12:05:00Z",
      seq: 1,
      entries: [
        {
          id: "d1",
          ts: "2026-05-21T12:00:00Z",
          action: "BET",
          side: "LAL ML",
          size_usd: 50,
          result: "WIN",
          pnl_usd: 47.6,
          reasoning: "Sentiment ramped early.",
        },
        {
          id: "d2",
          ts: "2026-05-21T12:02:00Z",
          action: "NO_BET",
          side: "BOS ML",
          result: "PENDING",
        },
      ],
    };
    act(() => {
      useWsStore.getState().ingest(frame);
    });

    expect(screen.getByTestId("decision-feed-count")).toHaveTextContent("2 rows");
    const rows = screen.getAllByTestId("decision-feed-row");
    expect(rows).toHaveLength(2);

    // Rows are newest-first.
    expect(rows[0]!.getAttribute("data-id")).toBe("d2");
    expect(rows[1]!.getAttribute("data-id")).toBe("d1");

    // WIN row exists with the right result attribute.
    const winRow = rows.find((r) => r.getAttribute("data-id") === "d1");
    expect(winRow).toBeTruthy();
    expect(winRow!.getAttribute("data-result")).toBe("WIN");
  });

  it("expands a row to show reasoning when clicked", () => {
    render(<DecisionFeed />);
    const frame: DecisionFeedMessage = {
      kind: "decision_feed",
      ts: "2026-05-21T12:05:00Z",
      seq: 1,
      entries: [
        {
          id: "d1",
          ts: "2026-05-21T12:00:00Z",
          action: "BET",
          side: "LAL ML",
          size_usd: 50,
          result: "WIN",
          pnl_usd: 47.6,
          reasoning: "Sentiment ramped early.",
          reflection: "Held discipline on size.",
        },
      ],
    };
    act(() => {
      useWsStore.getState().ingest(frame);
    });

    const row = screen.getByTestId("decision-feed-row");
    expect(screen.queryByTestId("decision-feed-row-detail")).toBeNull();

    fireEvent.click(within(row).getByTestId("decision-feed-row-toggle"));
    const detail = screen.getByTestId("decision-feed-row-detail");
    expect(detail).toHaveTextContent(/Sentiment ramped early/);
    expect(detail).toHaveTextContent(/Held discipline on size/);
  });

  it("merges a PENDING row in place when the WIN settlement arrives", () => {
    render(<DecisionFeed />);
    const initial: DecisionFeedMessage = {
      kind: "decision_feed",
      ts: "2026-05-21T12:00:00Z",
      seq: 1,
      entries: [
        {
          id: "d1",
          ts: "2026-05-21T12:00:00Z",
          action: "BET",
          side: "LAL ML",
          size_usd: 50,
          result: "PENDING",
        },
      ],
    };
    const settle: DecisionFeedMessage = {
      kind: "decision_feed",
      ts: "2026-05-21T12:30:00Z",
      seq: 2,
      entries: [
        {
          id: "d1",
          ts: "2026-05-21T12:29:00Z",
          action: "BET",
          side: "LAL ML",
          size_usd: 50,
          result: "WIN",
          pnl_usd: 47.6,
        },
      ],
    };
    act(() => {
      useWsStore.getState().ingest(initial);
      useWsStore.getState().ingest(settle);
    });
    // Still one row — merged by id, not duplicated.
    expect(screen.getAllByTestId("decision-feed-row")).toHaveLength(1);
    expect(screen.getByTestId("decision-feed-row").getAttribute("data-result")).toBe(
      "WIN",
    );
  });
});
