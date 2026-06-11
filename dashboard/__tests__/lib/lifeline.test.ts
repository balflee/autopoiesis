import { describe, expect, it } from "vitest";

import {
  hrefFor,
  LIFELINE_ORDER,
  nextStage,
  prevStage,
  STAGE_META,
} from "@/lib/lifeline";

/**
 * G1 — the lifeline metadata module is the single source of truth for the
 * abyss lifeline ORDER (roadmap → backtest → survival → mock) and the per-stage
 * shell chrome. These tests pin the order + the prev/next derivation so the
 * shared StageShell + footer cross-links never drift from one another.
 */
describe("lifeline order — single source of truth", () => {
  it("is hub-first: roadmap → backtest → survival → mock", () => {
    expect(LIFELINE_ORDER).toEqual(["roadmap", "backtest", "survival", "mock"]);
  });

  it("resolves every route's href", () => {
    expect(hrefFor("roadmap")).toBe("/roadmap");
    expect(hrefFor("backtest")).toBe("/backtest");
    expect(hrefFor("survival")).toBe("/survival");
    expect(hrefFor("mock")).toBe("/mock");
  });
});

describe("prev/next derive from the one order array", () => {
  it("backtest: prev is the hub (→ null, reached via header back-link), next is survival", () => {
    // The hub is reached via the shell's ◂ lifeline header link, not a footer
    // prev edge — so a stage whose predecessor is the hub has a null prev edge.
    expect(prevStage("backtest")).toBeNull();
    expect(nextStage("backtest")).toEqual({ href: "/survival", route: "survival" });
  });

  it("survival: prev is backtest, next is mock (the gated edge)", () => {
    expect(prevStage("survival")).toEqual({ href: "/backtest", route: "backtest" });
    expect(nextStage("survival")).toEqual({ href: "/mock", route: "mock" });
  });

  it("mock: prev is survival, next is null (last stage)", () => {
    expect(prevStage("mock")).toEqual({ href: "/survival", route: "survival" });
    expect(nextStage("mock")).toBeNull();
  });
});

describe("per-stage metadata pins each page's hero chrome", () => {
  it("backtest stage label + title + back-link testid", () => {
    const m = STAGE_META.backtest;
    expect(m.stageLabel).toBe("infancy · the seed policy");
    expect(m.title).toBe("BACKTEST");
    expect(m.backLinkTestId).toBe("backtest-back-link");
    expect(m.testId).toBe("backtest-route");
    expect(m.headerMarginClass).toBe("mb-16");
    expect(m.heroDelaysMs).toEqual({ eyebrow: 60, title: 120, subtitle: 240 });
  });

  it("survival stage label + title + back-link testid", () => {
    const m = STAGE_META.survival;
    expect(m.stageLabel).toBe("apprentice · learning to survive");
    expect(m.title).toBe("SURVIVAL");
    expect(m.backLinkTestId).toBe("survival-back-link");
    expect(m.testId).toBe("survival-route");
    expect(m.headerMarginClass).toBe("mb-12");
    // No hero stagger on survival (byte-identical to the page that had none).
    expect(m.heroDelaysMs).toBeUndefined();
  });

  it("mock stage label + title + back-link testid", () => {
    const m = STAGE_META.mock;
    expect(m.stageLabel).toBe("adult · paper-trading live");
    expect(m.title).toBe("MOCK BET");
    expect(m.backLinkTestId).toBe("mock-back-link");
    expect(m.testId).toBe("mock-route");
    expect(m.headerMarginClass).toBe("mb-12");
    expect(m.heroDelaysMs).toBeUndefined();
  });
});
