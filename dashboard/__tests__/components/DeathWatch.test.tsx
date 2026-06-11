/**
 * DeathWatch.test.tsx — T-D-004 acceptance specs.
 *
 * The acceptance brief asked these to live at `tests/dashboard/`, but
 * vitest/Vite cannot resolve `@testing-library/react` from a spec file
 * located outside the `dashboard/` package tree (Node module walk-up
 * never reaches `dashboard/node_modules`). The T-D-002 relocation
 * comment in `vitest.config.ts` records the same constraint. The .tsx
 * specs therefore live here under `dashboard/__tests__/components/`;
 * the golden fixture remains at `tests/dashboard/__fixtures__/` per
 * the brief so the T-B-010 dashboard_bridge producer can import it.
 *
 * Covers:
 *   1. Hidden until the trigger event lands (visible=false initially)
 *   2. Triggers on energy_threshold_crossed (direction='below', threshold=10)
 *   3. Sticky after terminal_lucidity_entered — stays mounted even if
 *      a later vitals frame reports breath > 10 (PRD §6.10)
 *   4. ipfs_degraded=true renders the degraded-badge UI string
 *      (PRD §5.1.C — silent fallback is a contract violation)
 *   5. LastWordsTypewriter + tombstone block mount on full event sequence
 */

import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import React from "react";

import "../setup";

import { DeathWatch } from "@/components/DeathWatch";
import { AbyssColors, ColorTokens } from "@/lib/colorTokens";
import { useWsStore } from "@/lib/wsStore";
import type {
  EnergyThresholdCrossedMessage,
  LastWordsEmittedMessage,
  TerminalLucidityEnteredMessage,
  TombstoneMintedMessage,
  VitalsMessage,
} from "@/lib/types";

// Shared golden fixture lives at the repo-root so T-B-010 can import it.
import fixtures from "../../../tests/dashboard/__fixtures__/death_watch_events.json";

const lower = (s: string | null | undefined): string => (s ?? "").toLowerCase();

/** Lift a #rgb/#rrggbb hex into the `rgb(r, g, b)` form jsdom normalises
 *  inline `style.color` to, so a token hex is assertable against a style. */
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

const energyBelow = fixtures.energy_threshold_crossed_below_10 as EnergyThresholdCrossedMessage;
const terminalEntered = fixtures.terminal_lucidity_entered as TerminalLucidityEnteredMessage;
const lastWordsWithTx = fixtures.last_words_emitted_with_tx as LastWordsEmittedMessage;
const tombstoneHappy = fixtures.tombstone_minted_happy_path as TombstoneMintedMessage;
const tombstoneDegraded = fixtures.tombstone_minted_ipfs_degraded as TombstoneMintedMessage;

const vitalsHealthy: VitalsMessage = {
  kind: "vitals",
  ts: "2026-05-22T03:50:00Z",
  seq: 900,
  payload: {
    breath: 60,
    bankroll: 150,
    countdown_s: 120,
    gas_per_min: 0.1,
    phase: "PHASE_3_MASTER",
  },
};

const vitalsRecovered: VitalsMessage = {
  kind: "vitals",
  ts: "2026-05-22T04:05:00Z",
  seq: 1010,
  payload: {
    breath: 45,
    bankroll: 150,
    countdown_s: 120,
    gas_per_min: 0.1,
    phase: "PHASE_3_MASTER",
  },
};

describe("DeathWatch — visibility + sticky terminal", () => {
  beforeEach(() => {
    useWsStore.getState().reset();
  });

  it("stays hidden until a death-watch trigger fires", () => {
    render(<DeathWatch />);
    act(() => {
      useWsStore.getState().ingest(vitalsHealthy);
    });
    const root = screen.getByTestId("death-watch-root");
    expect(root.getAttribute("data-visible")).toBe("false");
    expect(root.getAttribute("data-terminal-entered")).toBe("false");
    expect(screen.queryByTestId("death-watch-headline")).toBeNull();
  });

  it("becomes visible on energy_threshold_crossed below 10%", () => {
    render(<DeathWatch />);
    act(() => {
      useWsStore.getState().ingest(energyBelow);
    });
    const root = screen.getByTestId("death-watch-root");
    expect(root.getAttribute("data-visible")).toBe("true");
    expect(root.getAttribute("data-energy-pct")).toBe("9.4");
    // Pre-terminal heading copy.
    expect(screen.getByTestId("death-watch-headline").textContent).toMatch(
      /death watch/i,
    );
    expect(screen.queryByTestId("death-watch-sticky-badge")).toBeNull();
  });

  it("stays mounted after terminal_lucidity_entered even if breath recovers above 10 (PRD §6.10 sticky)", () => {
    render(<DeathWatch />);
    act(() => {
      useWsStore.getState().ingest(energyBelow);
      useWsStore.getState().ingest(terminalEntered);
    });
    // Now push a vitals frame showing breath > 10 — sticky flag must
    // keep DeathWatch on screen.
    act(() => {
      useWsStore.getState().ingest(vitalsRecovered);
    });
    const root = screen.getByTestId("death-watch-root");
    expect(root.getAttribute("data-visible")).toBe("true");
    expect(root.getAttribute("data-terminal-entered")).toBe("true");
    // Sticky badge surfaces so the audience knows there is no escape.
    expect(screen.getByTestId("death-watch-sticky-badge")).toBeInTheDocument();
    // Heading flips to the terminal copy.
    expect(screen.getByTestId("death-watch-headline").textContent).toMatch(
      /terminal lucidity/i,
    );
    // And the state slice confirms the latch was set.
    expect(useWsStore.getState().terminalLucidityEntered).toBe(true);
    expect(useWsStore.getState().terminalBreathAtEntry).toBe(9.8);
  });

  it("renders the ipfs_degraded badge when tombstone_minted has ipfs_degraded=true (PRD §5.1.C)", () => {
    render(<DeathWatch />);
    act(() => {
      useWsStore.getState().ingest(energyBelow);
      useWsStore.getState().ingest(terminalEntered);
      useWsStore.getState().ingest(tombstoneDegraded);
    });
    expect(screen.getByTestId("tombstone-mint-animation").getAttribute("data-ipfs-degraded")).toBe(
      "true",
    );
    expect(
      screen.getByTestId("tombstone-ipfs-degraded-badge").textContent,
    ).toMatch(/memory bank pin failed — text-only tombstone/);
    // The happy-path CID slot must NOT render alongside the degraded
    // badge — otherwise the demo audience cannot tell which path fired.
    expect(screen.queryByTestId("tombstone-ipfs-cid")).toBeNull();
  });

  it("variant='abyss' theming: the death takeover reads abyss, with NO navy leakage", () => {
    // FIX 2 — the permadeath CLIMAX is mounted on /mock with variant='abyss'.
    // The full-screen takeover must NOT leak the genesis-navy floor (#0B1426 /
    // rgba(11,20,38,…)) nor the genesis-ink text classes; it reconciles onto
    // the abyss --ab-* tokens instead.
    const { container } = render(<DeathWatch variant="abyss" />);
    act(() => {
      useWsStore.getState().ingest(energyBelow);
    });
    const root = screen.getByTestId("death-watch-root");
    expect(root.getAttribute("data-visible")).toBe("true");
    const markup = container.innerHTML;

    // No navy floor — neither the inline scrim rgba nor the token hex.
    expect(markup).not.toContain("rgba(11, 20, 38");
    expect(markup).not.toContain("rgba(11,20,38");
    expect(markup.toLowerCase()).not.toContain("#0b1426");
    // No genesis-ink / genesis-ink-muted structural classes survive.
    expect(markup).not.toContain("text-genesis-ink");
    expect(markup).not.toContain("genesis-ink-muted");

    // It DOES carry the abyss floor + abyss text tokens.
    expect(markup).toContain("rgba(6, 13, 11, 0.92)"); // scrim background
    expect(markup).toContain("rgba(6,13,11,0.92)"); // gradient outer stop
    expect(root.querySelector('[data-testid="death-watch-subline"]')?.className).toContain(
      "text-[var(--ab-text)]",
    );
    // The red reconciles to ab-death (#ff6b4a), not the legacy LOSS red.
    const headline = screen.getByTestId("death-watch-headline");
    expect(lower(headline.getAttribute("style"))).toContain(
      lower(hexToRgb(AbyssColors.DEATH)),
    );
  });

  it("variant='navy' (default) death takeover stays byte-identical (no abyss tokens)", () => {
    // The legacy /live takeover must be untouched: navy floor + genesis-ink.
    const { container } = render(<DeathWatch />);
    act(() => {
      useWsStore.getState().ingest(energyBelow);
    });
    const markup = container.innerHTML;
    // Navy floor scrim + gradient outer stop present (byte-unchanged).
    expect(markup).toContain("rgba(11, 20, 38, 0.92)");
    expect(markup).toContain("rgba(11,20,38,0.92)");
    // Legacy genesis-ink text classes present.
    expect(markup).toContain("text-genesis-ink");
    expect(markup).toContain("genesis-ink-muted");
    // No abyss tokens leaked into the navy default.
    expect(markup).not.toContain("var(--ab-");
    expect(markup).not.toContain("rgba(6, 13, 11");
    // Red is the legacy LOSS (#E63946), not ab-death.
    const headline = screen.getByTestId("death-watch-headline");
    expect(lower(headline.getAttribute("style"))).toContain(
      lower(hexToRgb(ColorTokens.LOSS)),
    );
  });

  it("renders LastWordsTypewriter + Tombstone block on full event sequence (happy path)", () => {
    render(<DeathWatch />);
    act(() => {
      useWsStore.getState().ingest(energyBelow);
      useWsStore.getState().ingest(terminalEntered);
      useWsStore.getState().ingest(lastWordsWithTx);
      useWsStore.getState().ingest(tombstoneHappy);
    });
    expect(screen.getByTestId("last-words-typewriter")).toBeInTheDocument();
    expect(screen.getByTestId("tombstone-mint-animation").getAttribute("data-token-id")).toBe(
      "1",
    );
    expect(screen.getByTestId("tombstone-ipfs-cid").textContent).toMatch(
      /bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi/,
    );
    // Last-words tx_hash receipt link surfaces.
    expect(
      screen.getByTestId("last-words-tx-hash").getAttribute("href"),
    ).toBe(
      `https://polygonscan.com/tx/${lastWordsWithTx.tx_hash}`,
    );
  });
});
