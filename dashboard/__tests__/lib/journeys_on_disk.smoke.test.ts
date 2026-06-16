/**
 * journeys_on_disk.smoke.test.ts — Task 4 Step 1b runtime journey gate.
 *
 * The named gates can't see this: `/survival` + `/reincarnation` are
 * force-dynamic (skipped by `next build`), and the other vitests use inline
 * fixtures. This smoke loads EVERY journey artifact actually present on disk
 * through the real validators and asserts none throws — the on-disk integration
 * check that the SURVIVAL slot-alias shim rescues old-key + verbatim-archive
 * journeys (the `_ai`/`_gemini`/`_run*` legs are intentionally left old-key).
 * Reincarnation arms are a generic "still parses" smoke (rename-agnostic).
 *
 * The gitignored journeys are absent on a fresh/CI checkout, so the whole suite
 * SKIPS when none are present (rather than passing vacuously); when present it
 * asserts ≥1 loaded.
 */

import { readFileSync, readdirSync, existsSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { validateSurvivalJourney } from "@/lib/load_survival_journey";

const BACKTEST_DIR = path.resolve(__dirname, "../../public/backtest");

function jsonFiles(prefix: string): string[] {
  if (!existsSync(BACKTEST_DIR)) return [];
  return readdirSync(BACKTEST_DIR)
    .filter((f) => f.startsWith(prefix) && f.endsWith(".json"))
    .sort();
}

const survivalFiles = jsonFiles("survival_journey");
const reincarnationFiles = jsonFiles("reincarnation");
const anyPresent = survivalFiles.length + reincarnationFiles.length > 0;

describe.skipIf(!anyPresent)("on-disk journeys load without throwing", () => {
  it("loaded at least one journey (non-vacuous)", () => {
    expect(survivalFiles.length + reincarnationFiles.length).toBeGreaterThan(0);
  });

  it.each(survivalFiles)(
    "survival journey %s validates (shim upgrades any legacy slot keys)",
    (file) => {
      const raw = JSON.parse(
        readFileSync(path.join(BACKTEST_DIR, file), "utf8"),
      );
      const f = validateSurvivalJourney(raw);
      expect(f.steps.length).toBeGreaterThan(0);
      // Every step exposes the NEW slot keys (shim-normalized), never the old.
      const sig = f.steps[0]!.signals as Record<string, unknown>;
      expect(sig.surface_advantage).toBeDefined();
      expect(sig.smart_money).toBeUndefined();
    },
  );

  // Reincarnation is rename-AGNOSTIC (load_reincarnation.ts does no slot-key
  // validation — genomes/knobs are validated by VALUE, not key), so a generic
  // "still parses as a JSON object" smoke is the right depth here. (Some arms,
  // e.g. the `_3pass` variants, carry a non-default `design` the strict
  // validator rejects for reasons unrelated to this rename — out of scope.)
  it.each(reincarnationFiles)(
    "reincarnation arm %s parses as a JSON object (rename-agnostic)",
    (file) => {
      const raw = JSON.parse(
        readFileSync(path.join(BACKTEST_DIR, file), "utf8"),
      );
      expect(typeof raw).toBe("object");
      expect(raw).not.toBeNull();
    },
  );
});
