import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import React from "react";

import "./setup";

import { LivingStageBody } from "../app/living/LivingStageBody";
import LivingRoute from "../app/living/page";
import { useWsStore } from "@/lib/wsStore";

/**
 * Living Stage (Layout A) — assembly smoke. Renders the body under the real
 * WsBootstrap → SandboxLiveBootstrap chain (no network in jsdom — the poll hook
 * no-ops without a fetcher) and asserts all five zones mount. Default state has
 * no decisionFeed, so The Act renders its idle "scanning" card.
 */
describe("LivingStageBody — five-zone assembly", () => {
  beforeEach(() => {
    useWsStore.getState().reset();
  });

  it("mounts all five zones", () => {
    render(<LivingStageBody />);
    expect(screen.getByTestId("living-organism")).toBeInTheDocument(); // Z1
    expect(screen.getByTestId("divine-event-stream")).toBeInTheDocument(); // Z2
    expect(screen.getByTestId("divine-treasury-total")).toBeInTheDocument(); // Z2
    expect(screen.getByTestId("act-idle")).toBeInTheDocument(); // Z3 (idle by default)
    expect(screen.getByTestId("fusion-rail")).toBeInTheDocument(); // Z4
    expect(screen.getByTestId("incarnation-lineage")).toBeInTheDocument(); // Z5
  });

  it("the /living route renders under the abyss design scope", () => {
    render(<LivingRoute />);
    expect(screen.getByTestId("living-route")).toHaveClass("abyss");
  });
});
