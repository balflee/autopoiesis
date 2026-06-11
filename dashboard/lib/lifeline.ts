/**
 * lifeline.ts — single source of truth for the ABYSS lifeline order +
 * per-stage shell metadata (G1).
 *
 * The four abyss pages tell the agent's lifecycle in order:
 *
 *   roadmap (hub) → backtest → survival → mock
 *
 * Every non-hub page used to copy-paste the same abyss shell (the back-link
 * to /roadmap, the top-right stage label, the eyebrow + serif title) and
 * hard-code its own prev/next cross-links. That duplicated the lifeline order
 * in four places. This module hoists the order + the per-stage chrome into one
 * typed table so {@link import("../components/lifeline/StageShell")} can render
 * all four through a single shell, and the prev/next edges derive from ONE
 * array — change the order here and every cross-link follows.
 *
 * The /mock "next" edge is the only GATED edge: the survival → mock link is
 * shown only when {@link import("./l5_gate").readL5Complete} is true (the same
 * fail-closed flag the /mock route + the roadmap node use). The metadata marks
 * that edge so the shell/footer can honour the gate without re-deriving order.
 */

import { MOCK_ROUTE } from "./l5_gate";

/** The routes that participate in the lifeline. `roadmap` is the hub/landing. */
export type LifelineRoute = "roadmap" | "backtest" | "survival" | "mock";

/**
 * The lifeline order, hub-first. The prev/next of any non-hub stage is its
 * neighbour in this array (the hub is index 0 and is every stage's ultimate
 * "back to lifeline" target, but is not itself a prev/next neighbour link —
 * the back-link to /roadmap is rendered separately in the shell header).
 */
export const LIFELINE_ORDER: readonly LifelineRoute[] = [
  "roadmap",
  "backtest",
  "survival",
  "mock",
] as const;

/** Per-stage chrome metadata for the shared {@link StageShell}. */
export interface StageMeta {
  /** the route key */
  route: LifelineRoute;
  /** href the route lives at (e.g. "/backtest") */
  href: string;
  /** the `main` data-testid (preserves each page's existing testid) */
  testId: string;
  /** the back-link data-testid (preserves each page's existing testid) */
  backLinkTestId: string;
  /** top-right stage label (lowercased, e.g. "infancy · the seed policy") */
  stageLabel: string;
  /** the agent's developmental phase word (Infancy / Apprentice / Adult) */
  agentPhase: string;
  /** the eyebrow line above the serif title */
  eyebrow: string;
  /** the serif display title (e.g. "BACKTEST") */
  title: string;
  /** the italic glow subtitle under the title */
  subtitle: string;
  /** header bottom-margin utility — backtest is mb-16, the rest mb-12 */
  headerMarginClass: "mb-16" | "mb-12";
  /**
   * Optional per-element `animation-delay` (ms) for the hero eyebrow / title /
   * subtitle. /backtest staggers its hero text (60/120/240ms); /survival and
   * /mock do NOT carry a delay style at all. Omitting a field renders NO inline
   * style on that element, byte-identical to the page that had none.
   */
  heroDelaysMs?: { eyebrow?: number; title?: number; subtitle?: number };
}

/**
 * The stage table, keyed by route. Hub (`roadmap`) is intentionally ABSENT —
 * the landing has its own bespoke centered hero (BreathWaveform, no back-link,
 * no stage label) and does NOT render through StageShell.
 */
export const STAGE_META: Readonly<Record<Exclude<LifelineRoute, "roadmap">, StageMeta>> = {
  backtest: {
    route: "backtest",
    href: "/backtest",
    testId: "backtest-route",
    backLinkTestId: "backtest-back-link",
    stageLabel: "infancy · the seed policy",
    agentPhase: "Infancy",
    eyebrow: "real-signal config sweep",
    title: "BACKTEST",
    subtitle:
      "the best seed the agent could be born with — before it has learned a thing.",
    headerMarginClass: "mb-16",
    heroDelaysMs: { eyebrow: 60, title: 120, subtitle: 240 },
  },
  survival: {
    route: "survival",
    href: "/survival",
    testId: "survival-route",
    backLinkTestId: "survival-back-link",
    stageLabel: "apprentice · learning to survive",
    agentPhase: "Apprentice",
    eyebrow: "l5 survival season",
    title: "SURVIVAL",
    subtitle: "it dies, and dies again — then learns to breathe.",
    headerMarginClass: "mb-12",
  },
  mock: {
    route: "mock",
    href: MOCK_ROUTE,
    testId: "mock-route",
    backLinkTestId: "mock-back-link",
    stageLabel: "adult · paper-trading live",
    agentPhase: "Adult",
    eyebrow: "live mock-bet · real prices, no risk",
    title: "MOCK BET",
    subtitle:
      "it has learned to breathe — now it trades the real market, for real stakes it cannot lose.",
    headerMarginClass: "mb-12",
  },
};

/** The cross-link edge to a neighbouring lifeline stage. */
export interface LifelineEdge {
  /** href to navigate to */
  href: string;
  /** the route key of the neighbour */
  route: LifelineRoute;
}

/**
 * The previous lifeline stage (toward the hub) for a non-hub route, or null
 * if the route's predecessor is the hub itself (which is reached via the
 * header back-link, not a footer prev edge). Derived from {@link LIFELINE_ORDER}.
 */
export function prevStage(route: Exclude<LifelineRoute, "roadmap">): LifelineEdge | null {
  const i = LIFELINE_ORDER.indexOf(route);
  const prev = i > 0 ? LIFELINE_ORDER[i - 1] : undefined;
  if (prev === undefined || prev === "roadmap") return null;
  return { href: hrefFor(prev), route: prev };
}

/**
 * The next lifeline stage for a non-hub route, or null if it is the last
 * stage. Derived from {@link LIFELINE_ORDER}. NOTE: the survival → mock edge
 * is gated (see {@link import("./l5_gate").readL5Complete}); this helper
 * reports the topological next regardless — the caller applies the gate.
 */
export function nextStage(route: Exclude<LifelineRoute, "roadmap">): LifelineEdge | null {
  const i = LIFELINE_ORDER.indexOf(route);
  const next = i >= 0 && i < LIFELINE_ORDER.length - 1 ? LIFELINE_ORDER[i + 1] : undefined;
  if (next === undefined) return null;
  return { href: hrefFor(next), route: next };
}

/** Resolve the href for any lifeline route key. */
export function hrefFor(route: LifelineRoute): string {
  if (route === "roadmap") return "/roadmap";
  return STAGE_META[route].href;
}
