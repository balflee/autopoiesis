/**
 * l5_gate.test.ts — F1 unit gate.
 *
 * The /mock (Page 3) route is gated behind "L5 complete". The gate is a
 * single typed env-flag resolver modelled on readEnvThreshold():
 * NEXT_PUBLIC_L5_COMPLETE, default FALSE, robust parse.
 *
 * Covers: unset → false, the canonical truthy spellings → true, the
 * canonical/garbage falsy spellings → false, and that MOCK_ROUTE is the
 * shared '/mock' constant both the roadmap gate and the F2 page consume.
 */

import { afterEach, describe, expect, it } from "vitest";

import { MOCK_ROUTE, readL5Complete } from "@/lib/l5_gate";

describe("readL5Complete — env flag resolver", () => {
  const ORIGINAL_ENV = process.env.NEXT_PUBLIC_L5_COMPLETE;

  afterEach(() => {
    if (ORIGINAL_ENV === undefined) {
      delete process.env.NEXT_PUBLIC_L5_COMPLETE;
    } else {
      process.env.NEXT_PUBLIC_L5_COMPLETE = ORIGINAL_ENV;
    }
  });

  it("unset env → false (safe default — L5 is not complete)", () => {
    delete process.env.NEXT_PUBLIC_L5_COMPLETE;
    expect(readL5Complete()).toBe(false);
  });

  it("'true' (and case/whitespace variants) → true", () => {
    process.env.NEXT_PUBLIC_L5_COMPLETE = "true";
    expect(readL5Complete()).toBe(true);
    process.env.NEXT_PUBLIC_L5_COMPLETE = "TRUE";
    expect(readL5Complete()).toBe(true);
    process.env.NEXT_PUBLIC_L5_COMPLETE = "  true  ";
    expect(readL5Complete()).toBe(true);
  });

  it("'1' and 'yes'/'on' truthy spellings → true", () => {
    process.env.NEXT_PUBLIC_L5_COMPLETE = "1";
    expect(readL5Complete()).toBe(true);
    process.env.NEXT_PUBLIC_L5_COMPLETE = "yes";
    expect(readL5Complete()).toBe(true);
    process.env.NEXT_PUBLIC_L5_COMPLETE = "on";
    expect(readL5Complete()).toBe(true);
  });

  it("'false', '0', empty, and garbage all → false", () => {
    process.env.NEXT_PUBLIC_L5_COMPLETE = "false";
    expect(readL5Complete()).toBe(false);
    process.env.NEXT_PUBLIC_L5_COMPLETE = "0";
    expect(readL5Complete()).toBe(false);
    process.env.NEXT_PUBLIC_L5_COMPLETE = "";
    expect(readL5Complete()).toBe(false);
    process.env.NEXT_PUBLIC_L5_COMPLETE = "maybe";
    expect(readL5Complete()).toBe(false);
  });

  it("exports the shared MOCK_ROUTE constant", () => {
    expect(MOCK_ROUTE).toBe("/mock");
  });
});
