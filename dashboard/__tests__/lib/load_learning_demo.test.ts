/**
 * load_learning_demo.test.ts — Stage-1 "能学" loader gate.
 *
 *   - LEARNING_DEMO   — the committed merged fixture parses + validates at
 *     module-eval and carries the three-arm result (frozen 0% / ema 100% /
 *     minimax 80%) + the weight ratchet + the gain sweep.
 *   - validateFixture — rejection paths (schema drift, missing arm, bad
 *     fraction). Happy path is the module-eval of LEARNING_DEMO itself.
 *
 * Mirrors load_static_sweep.test.ts; pure functions, no DOM.
 */

import { describe, expect, it } from "vitest";

import {
  LEARNING_ARM_KEYS,
  LEARNING_DEMO,
  LEARNING_DEMO_SCHEMA_VERSION,
  validateFixture,
} from "@/lib/load_learning_demo";

function cloneFixture(): Record<string, unknown> {
  return JSON.parse(JSON.stringify(LEARNING_DEMO)) as Record<string, unknown>;
}

describe("LEARNING_DEMO (bundled fixture)", () => {
  it("parses + validates the committed JSON at module-eval", () => {
    expect(LEARNING_DEMO.schema_version).toBe(LEARNING_DEMO_SCHEMA_VERSION);
    expect(Object.keys(LEARNING_DEMO.arms)).toEqual([...LEARNING_ARM_KEYS]);
  });

  it("carries the headline three-arm separation (frozen 0% / ema 100% / minimax 80%)", () => {
    expect(LEARNING_DEMO.arms.frozen.survival_rate).toBe(0);
    expect(LEARNING_DEMO.arms.ema.survival_rate).toBe(1);
    expect(LEARNING_DEMO.arms.minimax.survival_rate).toBeCloseTo(0.8, 6);
    // The frozen control never graduates; the learners do.
    expect(LEARNING_DEMO.arms.frozen.mean_surviving_incarnation).toBeNull();
    expect(LEARNING_DEMO.arms.ema.mean_surviving_incarnation).not.toBeNull();
  });

  it("every arm exposes one progress curve per seed", () => {
    for (const k of LEARNING_ARM_KEYS) {
      expect(LEARNING_DEMO.arms[k].curves.length).toBe(
        LEARNING_DEMO.config.seeds.length,
      );
    }
  });

  it("the weight ratchet lifts the edge slot and cuts the noise slot", () => {
    const wt = LEARNING_DEMO.weight_trajectory;
    expect(wt.edge_weight.length).toBe(wt.incarnations.length);
    expect(wt.noise_weight.length).toBe(wt.incarnations.length);
    const e0 = wt.edge_weight[0]!;
    const eN = wt.edge_weight[wt.edge_weight.length - 1]!;
    const n0 = wt.noise_weight[0]!;
    const nN = wt.noise_weight[wt.noise_weight.length - 1]!;
    expect(eN).toBeGreaterThan(e0); // edge slot climbs
    expect(nN).toBeLessThan(n0); // noise slot is cut
    expect(wt.minimax_quote.length).toBeGreaterThan(0);
  });

  it("the gain sweep flips the god's net positive at gain >= 0.2", () => {
    const rows = LEARNING_DEMO.gain_sweep;
    expect(rows.length).toBeGreaterThanOrEqual(4);
    const noise = rows.find((r) => r.gain === 0)!;
    const strong = rows.find((r) => r.gain === 0.5)!;
    expect(noise.net_vs_seed).toBeLessThan(0); // pure noise loses money
    expect(strong.net_vs_seed).toBeGreaterThan(0); // real edge pays
    const pivot = rows.find((r) => r.gain === 0.2)!;
    expect(pivot.net_vs_seed).toBeGreaterThanOrEqual(0);
  });

  it("foregrounds a synthetic-edge caveat", () => {
    expect(LEARNING_DEMO.caveat.toLowerCase()).toContain("synthetic");
  });
});

describe("validateFixture rejection paths", () => {
  it("rejects a schema_version mismatch", () => {
    const f = cloneFixture();
    f.schema_version = "9.9.9";
    expect(() => validateFixture(f)).toThrow(/schema_version mismatch/);
  });

  it("rejects a non-object root", () => {
    expect(() => validateFixture(null)).toThrow(/root must be an object/);
    expect(() => validateFixture([])).toThrow(/root must be an object/);
  });

  it("rejects a missing arm", () => {
    const f = cloneFixture();
    delete (f.arms as Record<string, unknown>).minimax;
    expect(() => validateFixture(f)).toThrow(/arms\.minimax must be an object/);
  });

  it("rejects an out-of-range survival_rate", () => {
    const f = cloneFixture();
    (((f.arms as Record<string, unknown>).frozen as Record<string, unknown>)
      .survival_rate as unknown) = 1.5;
    expect(() => validateFixture(f)).toThrow(/survival_rate must be in \[0, 1\]/);
  });

  it("rejects a missing gain_sweep array", () => {
    const f = cloneFixture();
    delete f.gain_sweep;
    expect(() => validateFixture(f)).toThrow(/gain_sweep must be an array/);
  });
});
