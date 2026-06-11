/**
 * death_watch_thresholds.ts — T-D-007 acceptance utility.
 *
 * Centralises the BREATH / countdown threshold math so the DeathWatchBorder
 * pulse + CountdownWidget tier transitions + their tests all reference one
 * pure source of truth (no duplicated `% < 10` literals scattered across
 * components).
 *
 * Why pure:
 *   - The brief requires ≥6 unit tests for the countdown calculator —
 *     making it a top-level pure function (no React, no timers, no
 *     window access) keeps the spec deterministic and trivial to test
 *     under vitest without a DOM.
 *   - Same module is consumed by both the LIVE DeathWatchBorder
 *     (visibility predicate) and the Demo §9 4:00-5:00 climax surface
 *     (the larger DeathWatch.tsx already keeps its own visibility seam
 *     via wsStore; the border is the EARLIER, gentler warning band
 *     that mounts before the full takeover).
 *
 * PRD anchors:
 *   - §8 "全屏切换 Death Watch 能量 < 10% 时触发" → the canonical
 *     BREATH_DEATH_WATCH_THRESHOLD = 10 (percentage of soft cap 100).
 *   - §5.1.A Desperate Mode 1h horizon — the countdown tiers below
 *     surface 1h / 10min / 5min / 1min visual escalation.
 *
 * Env override:
 *   `NEXT_PUBLIC_DEATH_WATCH_THRESHOLD_PCT` allows the orchestrator
 *   and Playwright runs to inject a higher trigger (e.g. 25 % for the
 *   border spec) without mutating production behaviour. The override
 *   is read at module load so it folds into the production build.
 *   Tests can also override at runtime via
 *   `window.__GENESIS_DEATH_WATCH_THRESHOLD__` which `getThreshold()`
 *   honours; both paths funnel through `readEnvThreshold()` so the
 *   precedence order (window > env > default) is one place to audit.
 */

/** Canonical demo trigger — 10 % of BREATH soft cap, per PRD §8. */
export const BREATH_DEATH_WATCH_THRESHOLD = 10;

/** Countdown-tier boundaries in seconds. Mirrors PRD §5.1.A escalation. */
export const COUNTDOWN_TIER_SECONDS = {
  /** Above this is "safe" — the countdown reads but does not chrome-shift. */
  TEN_MINUTES: 600,
  /** First red escalation tier — bold red text. */
  FIVE_MINUTES: 300,
  /** Final pre-death tier — pulsing red + AAA-contrast emphasis. */
  ONE_MINUTE: 60,
} as const;

/**
 * Tier names returned by {@link computeCountdown}. The DeathWatch border
 * + CountdownWidget components key their CSS class off this enum so the
 * tier→colour mapping is shared.
 *
 *   - "safe"      : seconds > 10 min
 *   - "warning"   : 5 min  < seconds ≤ 10 min  (amber)
 *   - "critical"  : 1 min  < seconds ≤  5 min  (loss-red, steady)
 *   - "imminent"  : 0      < seconds ≤  1 min  (loss-red, pulsing)
 *   - "expired"   : seconds ≤ 0 (Agent has died)
 */
export type CountdownTier =
  | "safe"
  | "warning"
  | "critical"
  | "imminent"
  | "expired";

export interface CountdownResult {
  /** Integer seconds remaining (floored). 0 when expired/negative. */
  readonly seconds_remaining: number;
  /** Pre-formatted `H:MM:SS` (1h+) or `MM:SS` (<1h). 00:00 when expired. */
  readonly formatted: string;
  /** Visual tier — components key chrome shifts off this. */
  readonly tier: CountdownTier;
}

/**
 * Pure countdown calculator — no React, no timers, no window.
 *
 *   seconds_remaining = floor((breath / burnRate) * 60)
 *
 * The burn rate is "energy units per minute" — matches the wire
 * `VitalsPayload.gas_per_min` field so the CountdownWidget can call
 * this with the vitals frame directly. Defensive against:
 *   - breath ≤ 0 → expired (already dead)
 *   - burnRate ≤ 0 → expired (no decay configured; treat as already
 *     finished so the UI does not show ∞ — the audience would parse
 *     "infinity" as a bug)
 *   - non-finite inputs → expired (NaN / Infinity from broken backend)
 *
 * @param breath   Current BREATH energy (PRD §4 — soft cap 100).
 * @param burnRate Energy units consumed per minute (effective_burn_rate).
 * @returns Deterministic CountdownResult — same inputs → same output.
 */
export function computeCountdown(
  breath: number,
  burnRate: number,
): CountdownResult {
  if (
    !Number.isFinite(breath) ||
    !Number.isFinite(burnRate) ||
    breath <= 0 ||
    burnRate <= 0
  ) {
    return { seconds_remaining: 0, formatted: "00:00", tier: "expired" };
  }
  // breath / burnRate gives MINUTES; * 60 → seconds; floor for stable text.
  const totalSeconds = Math.floor((breath / burnRate) * 60);
  return {
    seconds_remaining: totalSeconds,
    formatted: formatCountdown(totalSeconds),
    tier: tierFor(totalSeconds),
  };
}

/**
 * Format an integer second count as `H:MM:SS` (1h+) or `MM:SS` (<1h).
 * Negative / non-finite values render as `00:00` — the visible signal
 * that the Agent has expired (mirrors PRD §5.0 Death state).
 */
export function formatCountdown(totalSeconds: number): string {
  if (!Number.isFinite(totalSeconds) || totalSeconds <= 0) return "00:00";
  const s = Math.floor(totalSeconds);
  const hours = Math.floor(s / 3600);
  const minutes = Math.floor((s % 3600) / 60);
  const seconds = s % 60;
  const mm = minutes.toString().padStart(2, "0");
  const ss = seconds.toString().padStart(2, "0");
  if (hours > 0) return `${hours}:${mm}:${ss}`;
  return `${mm}:${ss}`;
}

/**
 * Map a countdown second count to its visual tier.
 *
 * Boundary semantics — STRICT INEQUALITIES on the upper side, so a
 * tick that lands exactly on 600 s shows the warning chrome (the
 * "transition fires AT 10min" requirement in the brief).
 */
export function tierFor(seconds: number): CountdownTier {
  if (!Number.isFinite(seconds) || seconds <= 0) return "expired";
  if (seconds <= COUNTDOWN_TIER_SECONDS.ONE_MINUTE) return "imminent";
  if (seconds <= COUNTDOWN_TIER_SECONDS.FIVE_MINUTES) return "critical";
  if (seconds <= COUNTDOWN_TIER_SECONDS.TEN_MINUTES) return "warning";
  return "safe";
}

/**
 * Resolve the active death-watch threshold (percentage of soft cap).
 *
 * Precedence (highest first):
 *   1. `window.__GENESIS_DEATH_WATCH_THRESHOLD__` — runtime override,
 *      set by Playwright via `addInitScript` for spec-time control.
 *   2. `process.env.NEXT_PUBLIC_DEATH_WATCH_THRESHOLD_PCT` — build-time
 *      override (Next.js inlines `NEXT_PUBLIC_*` env into the client
 *      bundle).
 *   3. `BREATH_DEATH_WATCH_THRESHOLD` (10) — production default.
 *
 * Invalid values (NaN / negative / >100) fall through to the default
 * — silent clamping is fine because the only path into invalid values
 * is a malformed env, and we don't want to break production over it.
 */
export function readEnvThreshold(): number {
  if (typeof window !== "undefined") {
    const w = window as unknown as {
      __GENESIS_DEATH_WATCH_THRESHOLD__?: number | string;
    };
    const raw = w.__GENESIS_DEATH_WATCH_THRESHOLD__;
    const parsed = typeof raw === "string" ? Number(raw) : raw;
    if (
      typeof parsed === "number" &&
      Number.isFinite(parsed) &&
      parsed > 0 &&
      parsed <= 100
    ) {
      return parsed;
    }
  }
  const env = process.env.NEXT_PUBLIC_DEATH_WATCH_THRESHOLD_PCT;
  if (typeof env === "string" && env.length > 0) {
    const n = Number(env);
    if (Number.isFinite(n) && n > 0 && n <= 100) return n;
  }
  return BREATH_DEATH_WATCH_THRESHOLD;
}

/**
 * Visibility predicate for {@link DeathWatchBorder}. The Border (the
 * gentler "you are entering Death Watch" red ring) appears as soon as
 * breath_pct dips below the active threshold; the full-screen DeathWatch
 * takeover continues to use its own selector (selectDeathWatchVisible)
 * which folds in the sticky terminal latch + threshold-crossed event.
 *
 * Keeping the border on a simpler predicate matches the acceptance
 * criterion verbatim: "DeathWatchBorder renders only when BREATH < 10%".
 */
export function isBorderVisible(
  breathPct: number | null,
  thresholdPct: number = readEnvThreshold(),
): boolean {
  if (breathPct == null || !Number.isFinite(breathPct)) return false;
  return breathPct < thresholdPct;
}
