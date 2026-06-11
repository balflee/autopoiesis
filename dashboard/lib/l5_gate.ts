/**
 * l5_gate.ts — F1 feature gate for the mock-bet route (Page 3).
 *
 * The /mock paper-trading surface is the "Adult" lifecycle stage and must
 * only become reachable once the L5 · LEARNING stage has completed. Rather
 * than hard-coding that state, we read a single typed env flag —
 * NEXT_PUBLIC_L5_COMPLETE — which Next.js inlines into the client bundle at
 * build (the canonical NEXT_PUBLIC_* pattern, mirrored on readEnvThreshold()
 * in death_watch_thresholds.ts).
 *
 * Default is FALSE: a missing / malformed flag means "L5 is not complete",
 * so the gate fails closed and the mock-bet stage stays locked. The roadmap
 * (server component) and the F2 /mock page both call readL5Complete() so the
 * gate logic lives in exactly one place.
 */

/** Shared route constant — consumed by the roadmap gate and the F2 page. */
export const MOCK_ROUTE = "/mock" as const;

/** Env var that unlocks the mock-bet stage when set to a truthy spelling. */
export const L5_COMPLETE_ENV = "NEXT_PUBLIC_L5_COMPLETE" as const;

/** Spellings we accept as "true". Everything else (incl. unset) is false. */
const TRUTHY = new Set(["true", "1", "yes", "on"]);

/**
 * Resolve the L5-complete gate from NEXT_PUBLIC_L5_COMPLETE.
 *
 * Robust parse: trims + lower-cases, accepts the common truthy spellings
 * ("true" / "1" / "yes" / "on"), and treats anything else — including unset,
 * empty, "false", "0", or garbage — as FALSE. Fails closed by design: an
 * accidental typo never unlocks the route.
 *
 * Reads `process.env` at call time (not module load) so server components
 * pick up the build-time inlined value and vitest can flip it per test.
 */
export function readL5Complete(): boolean {
  const raw = process.env[L5_COMPLETE_ENV];
  if (typeof raw !== "string") return false;
  return TRUTHY.has(raw.trim().toLowerCase());
}
