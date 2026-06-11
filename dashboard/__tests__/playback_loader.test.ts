/**
 * playback_loader.test.ts — T-D-006 unit suite.
 *
 * Pins the brief-mandated invariants on the new `PHASE2_DAY4_TWITTER_MISTAKE`
 * fixture + the helper utilities consumed by PlaybackPanel:
 *
 *   - schema_version matches the loader binding (0.1.0)
 *   - exactly 5 ticks numbered 845-849
 *   - climax tick 847: β₁ = 0.80, BET LAL $40, dwell_ms = 6000
 *   - dwell_ms timing: 1500 / 1500 / 6000 / 4000 / 5000
 *   - reflection tick 849 contains the canonical phrase verbatim
 *   - dominantSignal() returns β₁ + highlighted=true on tick 847 (delta > 0.3)
 *   - validatePlaybackFixture() rejects schema_version drift
 */

import { describe, expect, it } from "vitest";

import {
  DOMINANT_SIGNAL_DELTA_THRESHOLD,
  dominantSignal,
  PHASE2_DAY4_TWITTER_MISTAKE,
  PLAYBACK_FIXTURE_SCHEMA_VERSION,
  validatePlaybackFixture,
} from "../lib/playback_loader";

describe("PHASE2_DAY4_TWITTER_MISTAKE — fixture invariants (PRD §9)", () => {
  it("schema_version is bound to 0.1.0 by the loader", () => {
    expect(PLAYBACK_FIXTURE_SCHEMA_VERSION).toBe("0.1.0");
    expect(PHASE2_DAY4_TWITTER_MISTAKE.schema_version).toBe("0.1.0");
  });

  it("bundles exactly 5 ticks numbered 845-849", () => {
    expect(PHASE2_DAY4_TWITTER_MISTAKE.ticks).toHaveLength(5);
    const ids = PHASE2_DAY4_TWITTER_MISTAKE.ticks.map((t) => t.tick_id);
    expect(ids).toEqual([845, 846, 847, 848, 849]);
  });

  it("dwell_ms timing matches PRD §8 (1500/1500/6000/4000/5000)", () => {
    const dwells = PHASE2_DAY4_TWITTER_MISTAKE.ticks.map((t) => t.dwell_ms);
    expect(dwells).toEqual([1500, 1500, 6000, 4000, 5000]);
  });

  it("tick 847 is the climax: β₁ = 0.80, BET LAL $40, 6000 ms dwell", () => {
    const climax = PHASE2_DAY4_TWITTER_MISTAKE.ticks.find(
      (t) => t.tick_id === 847,
    );
    expect(climax).toBeDefined();
    expect(climax!.phase).toBe("climax");
    expect(climax!.signals.beta_1).toBeCloseTo(0.8, 5);
    expect(climax!.dwell_ms).toBe(6000);
    expect(climax!.decision).toEqual({
      action: "BET",
      side: "LAL",
      amount: 40,
      score: expect.any(Number),
      edge: expect.any(Number),
      rho_eff: expect.any(Number),
    });
  });

  it("tick 849 reflection contains the canonical PRD §9 phrase verbatim", () => {
    const reflection = PHASE2_DAY4_TWITTER_MISTAKE.ticks.find(
      (t) => t.tick_id === 849,
    );
    expect(reflection).toBeDefined();
    expect(reflection!.phase).toBe("reflection");
    expect(reflection!.reflection).toBe(
      "Never trust β₁ alone when α₃ is silent.",
    );
  });
});

describe("dominantSignal()", () => {
  it("returns β₁ + highlighted=true on tick 847 (delta > 0.3)", () => {
    const climax = PHASE2_DAY4_TWITTER_MISTAKE.ticks.find(
      (t) => t.tick_id === 847,
    )!;
    const dom = dominantSignal(climax.signals);
    expect(dom.key).toBe("beta_1");
    expect(dom.value).toBeCloseTo(0.8, 5);
    // β₁ = 0.80, second-highest = α₂ 0.41 (β₂ tuned to 0.42 deliberately
    // so the highlight rule clears the 0.3 PRD §8 threshold on the climax
    // tick while the surrounding ticks stay below it).
    expect(dom.delta).toBeGreaterThan(DOMINANT_SIGNAL_DELTA_THRESHOLD);
    expect(dom.highlighted).toBe(true);
  });

  it("non-climax ticks do NOT trip the amber highlight", () => {
    for (const t of PHASE2_DAY4_TWITTER_MISTAKE.ticks) {
      if (t.tick_id === 847) continue;
      expect(dominantSignal(t.signals).highlighted).toBe(false);
    }
  });

  it("flips highlighted=true when delta exceeds the 0.3 threshold", () => {
    const synthetic = {
      alpha_1: 0.1,
      alpha_2: 0.1,
      alpha_3: 0.1,
      beta_1: 0.85,
      beta_2: 0.2,
    };
    const dom = dominantSignal(synthetic);
    expect(dom.key).toBe("beta_1");
    expect(dom.delta).toBeCloseTo(0.65, 5);
    expect(dom.highlighted).toBe(true);
    expect(DOMINANT_SIGNAL_DELTA_THRESHOLD).toBe(0.3);
  });

  it("keeps highlighted=false on balanced signals", () => {
    const flat = {
      alpha_1: 0.5,
      alpha_2: 0.49,
      alpha_3: 0.48,
      beta_1: 0.51,
      beta_2: 0.5,
    };
    const dom = dominantSignal(flat);
    expect(dom.highlighted).toBe(false);
  });
});

describe("validatePlaybackFixture()", () => {
  function cloneRaw(): Record<string, unknown> {
    return JSON.parse(JSON.stringify(PHASE2_DAY4_TWITTER_MISTAKE)) as Record<
      string,
      unknown
    >;
  }

  it("returns the typed fixture for the bundled JSON", () => {
    const v = validatePlaybackFixture(cloneRaw());
    expect(v.schema_version).toBe("0.1.0");
  });

  it("throws on schema_version drift", () => {
    const bad = cloneRaw();
    bad.schema_version = "0.2.0";
    expect(() => validatePlaybackFixture(bad)).toThrow(/schema_version mismatch/);
  });

  it("throws on missing signals", () => {
    const bad = cloneRaw();
    const ticks = bad.ticks as Array<Record<string, unknown>>;
    ticks[0]!.signals = { alpha_1: 0.5 };
    expect(() => validatePlaybackFixture(bad)).toThrow(/signals/);
  });

  it("throws on dwell_ms out of range", () => {
    const bad = cloneRaw();
    const ticks = bad.ticks as Array<Record<string, unknown>>;
    ticks[0]!.dwell_ms = 999_999;
    expect(() => validatePlaybackFixture(bad)).toThrow(/dwell_ms/);
  });

  it("throws on > 12 ticks (legibility ceiling)", () => {
    const bad = cloneRaw();
    const ticks = bad.ticks as Array<Record<string, unknown>>;
    const seed = JSON.parse(JSON.stringify(ticks[0]));
    while (ticks.length < 13) ticks.push(JSON.parse(JSON.stringify(seed)));
    expect(() => validatePlaybackFixture(bad)).toThrow(/legibility ceiling/);
  });
});
