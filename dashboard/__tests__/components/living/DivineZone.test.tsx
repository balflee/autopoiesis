import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, beforeEach } from "vitest";
import React from "react";

import "../../setup";

import { DivineTreasury } from "@/components/living/DivineTreasury";
import { DivineEventStream } from "@/components/living/DivineEventStream";
import { useWsStore } from "@/lib/wsStore";
import type {
  GodsTreasuryRecordData,
  TitheRecordData,
  TributeRecordData,
} from "@/lib/sandbox_state_shared";

/**
 * Zone Z2 acceptance — DivineTreasury + DivineEventStream.
 *
 *   1. DivineTreasury renders the cumulative gods revenue, comma-grouped.
 *   2. DivineEventStream renders a tithe + a successful tribute, surfacing
 *      the TITHE / TRIBUTE / SURVIVED narrative tokens.
 */

describe("Divine zone (Z2)", () => {
  beforeEach(() => {
    useWsStore.getState().reset();
  });

  it("DivineTreasury shows the cumulative gods revenue", () => {
    act(() => {
      useWsStore.setState({ divineTreasury: 50719 } as never);
    });
    render(<DivineTreasury />);
    expect(screen.getByTestId("divine-treasury-total")).toHaveTextContent(
      "50,719",
    );
  });

  it("DivineEventStream renders tithe + surviving tribute cards", () => {
    const tithe: TitheRecordData = {
      type: "tithe",
      tithe_id: "ti-1",
      ts: "2026-06-18T12:00:00Z",
      tick: 10,
      paid_usd: 12.5,
      breath_cost: 0,
      breath_after: 80,
      bankroll_after: 900,
    };
    const tribute: TributeRecordData = {
      type: "tribute",
      tribute_id: "tr-1",
      ts: "2026-06-18T12:05:00Z",
      tick: 12,
      amount_usd: 50,
      success: true,
      breath_after: 70,
      bankroll_after: 850,
      dice_roll: 0.99,
    };
    const events: GodsTreasuryRecordData[] = [tithe, tribute];

    act(() => {
      useWsStore.setState({ divineEvents: events } as never);
    });
    render(<DivineEventStream />);

    const stream = screen.getByTestId("divine-event-stream");
    expect(stream).toHaveTextContent(/TITHE/i);
    expect(stream).toHaveTextContent(/TRIBUTE/i);
    expect(stream).toHaveTextContent(/SURVIVED/i);
  });
});
