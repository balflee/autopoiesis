/**
 * death_watch_thresholds.test.ts — T-D-007 unit gate.
 *
 * Acceptance demands ≥6 unit tests for the pure countdown calculator
 * covering: 1h horizon, 10min boundary, 5min boundary, 1min boundary,
 * sub-minute, negative (already-dead). We over-provision a few
 * additional cases (defensive inputs, formatCountdown direct, tierFor
 * direct, env override precedence) so a future schema bump does not
 * silently regress the calculator.
 */

import { afterEach, describe, expect, it } from "vitest";

import {
  BREATH_DEATH_WATCH_THRESHOLD,
  COUNTDOWN_TIER_SECONDS,
  computeCountdown,
  formatCountdown,
  isBorderVisible,
  readEnvThreshold,
  tierFor,
} from "@/lib/death_watch_thresholds";

describe("computeCountdown — 6 mandated cases", () => {
  it("1h horizon: breath 60, burn 1/min → 60:00 formatted as 1:00:00, tier 'safe'", () => {
    // 60 / 1 = 60 minutes = 3600 s. Above 10-min threshold → safe.
    const r = computeCountdown(60, 1);
    expect(r.seconds_remaining).toBe(3600);
    expect(r.formatted).toBe("1:00:00");
    expect(r.tier).toBe("safe");
  });

  it("10 min boundary: tier flips from 'safe' to 'warning' at exactly 600 s", () => {
    // breath=10, burn=1/min → 10 min = 600 s. Boundary belongs to warning
    // (strict-inequality on upper side per tierFor doc).
    const at = computeCountdown(10, 1);
    expect(at.seconds_remaining).toBe(600);
    expect(at.tier).toBe("warning");
    // One second above (601 s) must still be 'safe'.
    expect(tierFor(601)).toBe("safe");
    // One second below (599 s) is also 'warning'.
    expect(tierFor(599)).toBe("warning");
  });

  it("5 min boundary: tier flips from 'warning' to 'critical' at exactly 300 s", () => {
    // breath=5, burn=1/min → 5 min = 300 s.
    const at = computeCountdown(5, 1);
    expect(at.seconds_remaining).toBe(300);
    expect(at.formatted).toBe("05:00");
    expect(at.tier).toBe("critical");
    expect(tierFor(301)).toBe("warning");
    expect(tierFor(299)).toBe("critical");
  });

  it("1 min boundary: tier flips from 'critical' to 'imminent' at exactly 60 s", () => {
    // breath=1, burn=1/min → 1 min = 60 s.
    const at = computeCountdown(1, 1);
    expect(at.seconds_remaining).toBe(60);
    expect(at.formatted).toBe("01:00");
    expect(at.tier).toBe("imminent");
    expect(tierFor(61)).toBe("critical");
    expect(tierFor(59)).toBe("imminent");
  });

  it("sub-minute: 45 s remains 'imminent' and formats as 00:45", () => {
    // breath=0.75, burn=1/min → 0.75 min = 45 s.
    const r = computeCountdown(0.75, 1);
    expect(r.seconds_remaining).toBe(45);
    expect(r.formatted).toBe("00:45");
    expect(r.tier).toBe("imminent");
  });

  it("negative / already-dead: returns 00:00 + tier 'expired'", () => {
    // breath ≤ 0 → expired regardless of burn rate.
    const dead = computeCountdown(0, 1);
    expect(dead.seconds_remaining).toBe(0);
    expect(dead.formatted).toBe("00:00");
    expect(dead.tier).toBe("expired");

    const negative = computeCountdown(-5, 1);
    expect(negative.seconds_remaining).toBe(0);
    expect(negative.formatted).toBe("00:00");
    expect(negative.tier).toBe("expired");
  });
});

describe("computeCountdown — defensive inputs", () => {
  it("non-finite breath (NaN, Infinity) → expired", () => {
    expect(computeCountdown(Number.NaN, 1).tier).toBe("expired");
    expect(computeCountdown(Number.POSITIVE_INFINITY, 1).tier).toBe("expired");
  });

  it("zero or negative burn rate → expired (no decay = no countdown to render)", () => {
    expect(computeCountdown(50, 0).tier).toBe("expired");
    expect(computeCountdown(50, -1).tier).toBe("expired");
  });
});

describe("formatCountdown — direct format coverage", () => {
  it("zero, negative, NaN all render as 00:00", () => {
    expect(formatCountdown(0)).toBe("00:00");
    expect(formatCountdown(-1)).toBe("00:00");
    expect(formatCountdown(Number.NaN)).toBe("00:00");
  });

  it("under one hour drops the hour prefix", () => {
    expect(formatCountdown(7)).toBe("00:07");
    expect(formatCountdown(59)).toBe("00:59");
    expect(formatCountdown(150)).toBe("02:30");
    expect(formatCountdown(3599)).toBe("59:59");
  });

  it("one hour or more renders H:MM:SS", () => {
    expect(formatCountdown(3600)).toBe("1:00:00");
    expect(formatCountdown(3661)).toBe("1:01:01");
    expect(formatCountdown(7325)).toBe("2:02:05");
  });
});

describe("tierFor — boundary table", () => {
  it("expired only on ≤ 0", () => {
    expect(tierFor(0)).toBe("expired");
    expect(tierFor(-100)).toBe("expired");
    expect(tierFor(Number.NaN)).toBe("expired");
  });

  it("imminent on (0, 60]", () => {
    expect(tierFor(1)).toBe("imminent");
    expect(tierFor(60)).toBe("imminent");
  });

  it("critical on (60, 300]", () => {
    expect(tierFor(61)).toBe("critical");
    expect(tierFor(COUNTDOWN_TIER_SECONDS.FIVE_MINUTES)).toBe("critical");
  });

  it("warning on (300, 600]", () => {
    expect(tierFor(301)).toBe("warning");
    expect(tierFor(COUNTDOWN_TIER_SECONDS.TEN_MINUTES)).toBe("warning");
  });

  it("safe above 600", () => {
    expect(tierFor(601)).toBe("safe");
    expect(tierFor(7200)).toBe("safe");
  });
});

describe("readEnvThreshold + isBorderVisible", () => {
  const ORIGINAL_ENV = process.env.NEXT_PUBLIC_DEATH_WATCH_THRESHOLD_PCT;

  afterEach(() => {
    // Reset both override paths to keep tests independent.
    if (ORIGINAL_ENV === undefined) {
      delete process.env.NEXT_PUBLIC_DEATH_WATCH_THRESHOLD_PCT;
    } else {
      process.env.NEXT_PUBLIC_DEATH_WATCH_THRESHOLD_PCT = ORIGINAL_ENV;
    }
    if (typeof window !== "undefined") {
      delete (
        window as unknown as { __GENESIS_DEATH_WATCH_THRESHOLD__?: unknown }
      ).__GENESIS_DEATH_WATCH_THRESHOLD__;
    }
  });

  it("default threshold is the PRD §8 canonical 10 %", () => {
    delete process.env.NEXT_PUBLIC_DEATH_WATCH_THRESHOLD_PCT;
    expect(readEnvThreshold()).toBe(BREATH_DEATH_WATCH_THRESHOLD);
    expect(BREATH_DEATH_WATCH_THRESHOLD).toBe(10);
  });

  it("NEXT_PUBLIC env overrides the default", () => {
    process.env.NEXT_PUBLIC_DEATH_WATCH_THRESHOLD_PCT = "25";
    expect(readEnvThreshold()).toBe(25);
  });

  it("invalid env values silently fall back to the default", () => {
    process.env.NEXT_PUBLIC_DEATH_WATCH_THRESHOLD_PCT = "not-a-number";
    expect(readEnvThreshold()).toBe(BREATH_DEATH_WATCH_THRESHOLD);
    process.env.NEXT_PUBLIC_DEATH_WATCH_THRESHOLD_PCT = "-5";
    expect(readEnvThreshold()).toBe(BREATH_DEATH_WATCH_THRESHOLD);
    process.env.NEXT_PUBLIC_DEATH_WATCH_THRESHOLD_PCT = "200";
    expect(readEnvThreshold()).toBe(BREATH_DEATH_WATCH_THRESHOLD);
  });

  it("window override takes precedence over env (Playwright runtime path)", () => {
    process.env.NEXT_PUBLIC_DEATH_WATCH_THRESHOLD_PCT = "25";
    (
      window as unknown as { __GENESIS_DEATH_WATCH_THRESHOLD__?: number }
    ).__GENESIS_DEATH_WATCH_THRESHOLD__ = 50;
    expect(readEnvThreshold()).toBe(50);
  });

  it("isBorderVisible: null breath → false (still booting)", () => {
    expect(isBorderVisible(null)).toBe(false);
  });

  it("isBorderVisible: breath ≥ threshold → false, breath < threshold → true", () => {
    expect(isBorderVisible(15)).toBe(false); // above default 10
    expect(isBorderVisible(10)).toBe(false); // EQUAL → not yet
    expect(isBorderVisible(9.5)).toBe(true);
    expect(isBorderVisible(0)).toBe(true);
  });

  it("isBorderVisible honours an explicit threshold arg", () => {
    expect(isBorderVisible(20, 25)).toBe(true);
    expect(isBorderVisible(30, 25)).toBe(false);
  });
});
