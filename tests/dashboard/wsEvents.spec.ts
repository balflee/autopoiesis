/**
 * wsEvents.spec.ts — pins the four death-watch type guards against the
 * golden fixture. If a fixture frame ever stops matching the guard
 * (because the producer drifted or the consumer narrowed too tight),
 * this spec fails before the surface does.
 */

import { describe, expect, it } from "vitest";

import {
  isDeathWatchMessage,
  isEnergyThresholdCrossed,
  isLastWordsEmitted,
  isTerminalLucidityEntered,
  isTombstoneMinted,
  DEATH_WATCH_KINDS,
} from "@/lib/wsEvents";

import fixtures from "./__fixtures__/death_watch_events.json";

describe("wsEvents — death-watch type guards vs golden fixture", () => {
  it("DEATH_WATCH_KINDS lists exactly the four expected kinds", () => {
    expect([...DEATH_WATCH_KINDS].sort()).toEqual([
      "energy_threshold_crossed",
      "last_words_emitted",
      "terminal_lucidity_entered",
      "tombstone_minted",
    ]);
  });

  it("isEnergyThresholdCrossed validates both above + below crossings", () => {
    expect(isEnergyThresholdCrossed(fixtures.energy_threshold_crossed_below_10)).toBe(true);
    expect(isEnergyThresholdCrossed(fixtures.energy_threshold_crossed_above_10)).toBe(true);
  });

  it("isEnergyThresholdCrossed rejects bad direction + out-of-range pct", () => {
    expect(
      isEnergyThresholdCrossed({
        kind: "energy_threshold_crossed",
        ts: "2026-05-22T04:00:00Z",
        seq: 1,
        energy_pct: 5,
        threshold_pct: 10,
        direction: "sideways",
      }),
    ).toBe(false);
    expect(
      isEnergyThresholdCrossed({
        kind: "energy_threshold_crossed",
        ts: "2026-05-22T04:00:00Z",
        seq: 1,
        energy_pct: 150,
        threshold_pct: 10,
        direction: "below",
      }),
    ).toBe(false);
  });

  it("isTerminalLucidityEntered validates breath_at_entry presence", () => {
    expect(isTerminalLucidityEntered(fixtures.terminal_lucidity_entered)).toBe(true);
    expect(
      isTerminalLucidityEntered({
        kind: "terminal_lucidity_entered",
        ts: "2026-05-22T04:00:00Z",
        seq: 1,
      }),
    ).toBe(false);
  });

  it("isLastWordsEmitted accepts with-tx and without-tx, rejects bad hex hash", () => {
    expect(isLastWordsEmitted(fixtures.last_words_emitted_with_tx)).toBe(true);
    expect(isLastWordsEmitted(fixtures.last_words_emitted_no_tx)).toBe(true);
    expect(
      isLastWordsEmitted({
        kind: "last_words_emitted",
        ts: "2026-05-22T04:30:00Z",
        seq: 1,
        text: "hi",
        tx_hash: "0xnot-hex",
      }),
    ).toBe(false);
  });

  it("isTombstoneMinted accepts both happy and ipfs_degraded paths", () => {
    expect(isTombstoneMinted(fixtures.tombstone_minted_happy_path)).toBe(true);
    expect(isTombstoneMinted(fixtures.tombstone_minted_ipfs_degraded)).toBe(true);
  });

  it("isTombstoneMinted rejects missing ipfs_degraded boolean", () => {
    expect(
      isTombstoneMinted({
        kind: "tombstone_minted",
        ts: "2026-05-22T04:31:00Z",
        seq: 1,
        token_id: "1",
      }),
    ).toBe(false);
  });

  it("isDeathWatchMessage union guard recognises every fixture entry", () => {
    const candidates = [
      fixtures.energy_threshold_crossed_below_10,
      fixtures.energy_threshold_crossed_above_10,
      fixtures.terminal_lucidity_entered,
      fixtures.last_words_emitted_with_tx,
      fixtures.last_words_emitted_no_tx,
      fixtures.tombstone_minted_happy_path,
      fixtures.tombstone_minted_ipfs_degraded,
    ];
    for (const c of candidates) {
      expect(isDeathWatchMessage(c)).toBe(true);
    }
    expect(isDeathWatchMessage({ kind: "vitals", ts: "x", seq: 1 })).toBe(false);
  });
});
