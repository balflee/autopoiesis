import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, beforeEach } from "vitest";
import React from "react";

import "../setup";

import { DualEngineMeter } from "../../components/DualEngineMeter";
import { useWsStore } from "../../lib/wsStore";
import type { WeightsUpdatedMessage } from "../../lib/types";

const weightsFrame = (
  overrides: Partial<WeightsUpdatedMessage["weights"]> = {},
): WeightsUpdatedMessage => ({
  kind: "weights_updated",
  ts: "2026-05-21T12:00:00Z",
  seq: 1,
  weights: {
    w_r: 0.7,
    w_s: 0.3,
    alpha: 0.62,
    beta: 0,
    rho: 0.05,
    ...overrides,
  },
});

describe("DualEngineMeter — loading + rendering", () => {
  beforeEach(() => {
    useWsStore.getState().reset();
  });

  it("renders the loading skeleton when no weights frame has been received yet", () => {
    render(<DualEngineMeter />);
    const meter = screen.getByTestId("dual-engine-meter");
    expect(meter.getAttribute("data-loading")).toBe("true");
    expect(meter).toHaveTextContent(/waiting for weights frame/i);
  });

  it("greys out β₁ as FROZEN when Phase 1 ships β=0 (TP §5.3 Phase 1 freeze)", () => {
    render(<DualEngineMeter />);
    act(() => {
      useWsStore.getState().ingest(weightsFrame({ beta: 0 }));
    });
    const beta = screen.getByTestId("dual-engine-beta");
    expect(beta.getAttribute("data-frozen")).toBe("true");
    expect(screen.getByTestId("dual-engine-beta-value")).toHaveTextContent(/frozen/i);
  });

  it("renders proportional W_R / W_S band widths summing to 100 %", () => {
    render(<DualEngineMeter />);
    act(() => {
      useWsStore.getState().ingest(weightsFrame({ w_r: 0.4, w_s: 0.6 }));
    });
    const ruleFill = screen.getByTestId("dual-engine-rule-share");
    const signalFill = screen.getByTestId("dual-engine-signal-share");
    expect(ruleFill.getAttribute("style")).toMatch(/width:\s*40/);
    expect(signalFill.getAttribute("style")).toMatch(/width:\s*60/);
  });

  it("shows signal-led label once w_s > 0.7 and surfaces β₁ value", () => {
    render(<DualEngineMeter />);
    act(() => {
      useWsStore.getState().ingest(
        weightsFrame({ w_r: 0.2, w_s: 0.8, beta: 0.8 }),
      );
    });
    expect(screen.getByTestId("dual-engine-dominance")).toHaveTextContent(/signal-led/i);
    expect(screen.getByTestId("dual-engine-beta").getAttribute("data-frozen")).toBe("false");
    expect(screen.getByTestId("dual-engine-beta-value")).toHaveTextContent("0.80");
  });
});
