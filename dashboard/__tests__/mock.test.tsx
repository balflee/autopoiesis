import { act, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import React from "react";

import "./setup";

import MockRoute from "../app/mock/page";
import { AbyssColors, ColorTokens } from "@/lib/colorTokens";
import { DecisionFeed } from "@/components/DecisionFeed";
import { useWsStore } from "@/lib/wsStore";
import type { DecisionFeedEntry, DecisionFeedMessage } from "@/lib/types";

/**
 * Page-3 (F2) — the LIVE mock-bet page at /mock.
 *
 * The route is a server component gated by F1's readL5Complete()
 * (NEXT_PUBLIC_L5_COMPLETE). This suite pins the three F2 acceptance beats:
 *
 *   (a) UNSET env → the locked empty-state (`mock-locked`) renders and NONE of
 *       the live widgets mount, so even a deep-link is blocked.
 *   (b) SET env → after ingesting a v0.3.0 decision_feed entry carrying
 *       market_id + signals, the DecisionFeed surfaces the market_id and the
 *       per-engine signal block.
 *   (c) the reused widgets render under variant='abyss' without crashing.
 *
 * Plus a variant-prop guard proving DecisionFeed's default ('navy') output is
 * unchanged (legacy /live stays byte-identical).
 *
 * Mirrors the roadmap/backtest smoke pattern: RTL + `import "./setup"`.
 */

const lower = (s: string | null | undefined): string => (s ?? "").toLowerCase();

/**
 * jsdom normalises an inline `style.color` hex to `rgb(r, g, b)`. To assert a
 * concrete ColorTokens/AbyssColors hex against an inline style we lift the hex
 * into the same `rgb()` space. Supports #rgb and #rrggbb.
 */
function hexToRgb(hex: string): string {
  let h = hex.replace("#", "");
  if (h.length === 3) {
    h = h
      .split("")
      .map((c) => c + c)
      .join("");
  }
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return `rgb(${r}, ${g}, ${b})`;
}

/** A fully-populated v0.3.0 decision_feed frame (market_id + 5 signals). */
function v030Frame(): DecisionFeedMessage {
  return {
    kind: "decision_feed",
    ts: "2026-06-08T12:05:00Z",
    seq: 1,
    entries: [
      {
        id: "m1",
        ts: "2026-06-08T12:00:00Z",
        action: "BET",
        side: "Alcaraz ML",
        size_usd: 4,
        result: "PENDING",
        reasoning: "Edge on the favorite under surface advantage.",
        market_id: "0xMARKET_ALCARAZ_SINNER",
        signals: {
          tennis_technical: 0.42,
          market_momentum: -0.15,
          smart_money: 0.31,
          sentiment_llm: 0.08,
          crowd_volume: -0.22,
        },
      },
    ],
  };
}

/**
 * A wide frame (> VISIBLE_ROWS=8 entries) so the overflow `<details>` "show
 * all" disclosure renders — needed to assert its faint /20 border.
 */
function wideFrame(n = 12): DecisionFeedMessage {
  const base = v030Frame().entries[0] as DecisionFeedEntry;
  return {
    kind: "decision_feed",
    ts: "2026-06-08T12:05:00Z",
    seq: 1,
    entries: Array.from(
      { length: n },
      (_, i): DecisionFeedEntry => ({
        ...base,
        id: `m${i}`,
      }),
    ),
  };
}

describe("MockRoute — F2 L5 gate (deep-link block)", () => {
  const ORIGINAL_ENV = process.env.NEXT_PUBLIC_L5_COMPLETE;

  beforeEach(() => {
    useWsStore.getState().reset();
  });

  afterEach(() => {
    if (ORIGINAL_ENV === undefined) {
      delete process.env.NEXT_PUBLIC_L5_COMPLETE;
    } else {
      process.env.NEXT_PUBLIC_L5_COMPLETE = ORIGINAL_ENV;
    }
  });

  it("when L5 is NOT complete: renders the locked empty-state and NO live widgets", () => {
    delete process.env.NEXT_PUBLIC_L5_COMPLETE;
    render(<MockRoute />);

    expect(screen.getByTestId("mock-locked")).toBeInTheDocument();
    // The live widgets must NOT mount behind the gate (deep-link blocked).
    expect(screen.queryByTestId("vitals-panel")).toBeNull();
    expect(screen.queryByTestId("dual-engine-meter")).toBeNull();
    expect(screen.queryByTestId("decision-feed")).toBeNull();
    expect(screen.queryByTestId("consciousness-live-stub")).toBeNull();
  });

  it("when L5 IS complete: surfaces market_id + the per-engine signal block from a v0.3.0 frame", () => {
    process.env.NEXT_PUBLIC_L5_COMPLETE = "true";
    render(<MockRoute />);

    // Gate is open: the live widgets mount, no locked state.
    expect(screen.queryByTestId("mock-locked")).toBeNull();
    expect(screen.getByTestId("decision-feed")).toBeInTheDocument();

    act(() => {
      useWsStore.getState().ingest(v030Frame());
    });

    const row = screen.getByTestId("decision-feed-row");
    // market_id is surfaced (inline, no expand needed).
    const market = within(row).getByTestId("decision-feed-row-market");
    expect(market).toHaveTextContent("0xMARKET_ALCARAZ_SINNER");

    // Expand to reveal the structured per-engine signal block.
    act(() => {
      within(row).getByTestId("decision-feed-row-toggle").click();
    });
    const signals = within(row).getByTestId("decision-feed-row-signals");
    expect(signals).toBeInTheDocument();
    // All 5 lowercase engine keys render a score chip.
    for (const key of [
      "tennis_technical",
      "market_momentum",
      "smart_money",
      "sentiment_llm",
      "crowd_volume",
    ]) {
      expect(
        within(signals).getByTestId(`decision-feed-signal-${key}`),
      ).toBeInTheDocument();
    }
  });

  it("when L5 IS complete: the abyss-skinned live widgets mount without crashing", () => {
    process.env.NEXT_PUBLIC_L5_COMPLETE = "true";
    render(<MockRoute />);

    // The page renders under the .abyss design scope with the hero.
    const page = screen.getByTestId("mock-route");
    expect(page).toHaveClass("abyss");

    // Store-driven widgets are present (in their loading state pre-ingest).
    expect(screen.getByTestId("vitals-panel")).toBeInTheDocument();
    expect(screen.getByTestId("dual-engine-meter")).toBeInTheDocument();
    expect(screen.getByTestId("decision-feed")).toBeInTheDocument();
    expect(screen.getByTestId("consciousness-live-stub")).toBeInTheDocument();
  });
});

/**
 * G1 — the shared lifeline StageShell renders the mock stage's chrome: the
 * ADULT stage label, the ◂ lifeline back-link to /roadmap, and (once unlocked)
 * the footer's backward cross-link to the previous lifeline stage (◂ back to
 * survival → /survival). The locked empty-state renders the same header chrome
 * but NO footer (byte-identical to the previously-inlined Shell).
 */
describe("MockRoute — shared StageShell nav (G1)", () => {
  const ORIGINAL_ENV = process.env.NEXT_PUBLIC_L5_COMPLETE;

  beforeEach(() => {
    useWsStore.getState().reset();
  });

  afterEach(() => {
    if (ORIGINAL_ENV === undefined) {
      delete process.env.NEXT_PUBLIC_L5_COMPLETE;
    } else {
      process.env.NEXT_PUBLIC_L5_COMPLETE = ORIGINAL_ENV;
    }
  });

  it("renders the adult stage label + ◂ lifeline back-link to /roadmap (locked)", () => {
    delete process.env.NEXT_PUBLIC_L5_COMPLETE;
    render(<MockRoute />);

    expect(screen.getByText("adult · paper-trading live")).toBeInTheDocument();
    const back = screen.getByTestId("mock-back-link");
    expect(back).toHaveTextContent("◂ lifeline");
    expect(back.getAttribute("href")).toBe("/roadmap");
    // Locked state mounts NO footer cross-link.
    expect(screen.queryByText(/back to survival/i)).toBeNull();
  });

  it("when unlocked: backward-links to the previous lifeline stage (/survival)", () => {
    process.env.NEXT_PUBLIC_L5_COMPLETE = "true";
    render(<MockRoute />);

    const prev = screen
      .getByText(/back to survival/i)
      .closest("a") as HTMLAnchorElement | null;
    expect(prev).not.toBeNull();
    expect(prev?.getAttribute("href")).toBe("/survival");
  });

  it("when unlocked: the backward /survival link uses the DIM accent (BackLinkDim, not the glow NextLink)", () => {
    // G follow-up — backward lifeline edges render in the dim accent
    // (text-[var(--ab-dim)]), matching survival's "◂ back to the seed". The
    // /mock footer previously rendered this backward link with the glow
    // NextLink (text-[var(--ab-glow)]/80); switching it to BackLinkDim keeps
    // backward edges visually consistent across the lifeline.
    process.env.NEXT_PUBLIC_L5_COMPLETE = "true";
    render(<MockRoute />);

    const prev = screen
      .getByText(/back to survival/i)
      .closest("a") as HTMLAnchorElement | null;
    expect(prev).not.toBeNull();
    // DIM styling present…
    expect(prev?.className).toContain("text-[var(--ab-dim)]");
    // …and the glow accent (the forward NextLink class) is NOT applied.
    expect(prev?.className).not.toContain("text-[var(--ab-glow)]/80");
    // The ◂ direction glyph + href stay unchanged.
    expect(prev?.textContent).toContain("◂");
    expect(prev?.getAttribute("href")).toBe("/survival");
  });
});

describe("DecisionFeed — variant prop (additive, navy unchanged)", () => {
  beforeEach(() => {
    useWsStore.getState().reset();
  });

  it("default variant='navy' keeps the legacy genesis palette (byte-unchanged)", () => {
    render(<DecisionFeed />);
    act(() => {
      useWsStore.getState().ingest(wideFrame());
    });
    const root = screen.getByTestId("decision-feed");
    // Legacy navy classes are present.
    expect(root.className).toContain("border-genesis-ink-muted/30");
    expect(root.className).toContain("text-genesis-ink");

    // F2 follow-up regression guard: the two /live-reachable inner borders
    // MUST stay at the legacy /20 opacity (the shared navy palette had drifted
    // them to /30). (a) the overflow "show all" <details>…
    const overflow = screen.getByTestId("decision-feed-overflow");
    expect(overflow.className).toContain("border-genesis-ink-muted/20");
    expect(overflow.className).not.toContain("border-genesis-ink-muted/30");

    // …and (b) the expanded row-detail panel.
    const firstRow = screen.getAllByTestId("decision-feed-row")[0]!;
    act(() => {
      within(firstRow).getByTestId("decision-feed-row-toggle").click();
    });
    const detail = within(firstRow).getByTestId("decision-feed-row-detail");
    expect(detail.className).toContain("border-genesis-ink-muted/20");
    expect(detail.className).not.toContain("border-genesis-ink-muted/30");

    // The PENDING dot uses the legacy AMBER literal.
    const result = screen.getAllByTestId("decision-feed-row-result")[0]!;
    expect(lower(result.getAttribute("style"))).toContain(
      lower(hexToRgb(ColorTokens.AMBER)),
    );
  });

  it("variant='abyss' recolors structure + accents to the bioluminescent palette", () => {
    render(<DecisionFeed variant="abyss" />);
    act(() => {
      useWsStore.getState().ingest(v030Frame());
    });
    const root = screen.getByTestId("decision-feed");
    // No legacy navy structural class survives.
    expect(root.className).not.toContain("genesis-ink-muted");
    expect(root.className).toContain("var(--ab-");
    // The PENDING accent is now the lime glow, not amber.
    const result = screen.getByTestId("decision-feed-row-result");
    expect(lower(result.getAttribute("style"))).toContain(
      lower(hexToRgb(AbyssColors.GLOW)),
    );
    expect(lower(result.getAttribute("style"))).not.toContain(
      lower(hexToRgb(ColorTokens.AMBER)),
    );
  });
});
