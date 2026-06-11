import Link from "next/link";
import type { JSX } from "react";

import { MOCK_ROUTE, readL5Complete } from "@/lib/l5_gate";

/**
 * /roadmap — the landing.
 *
 * Phase C redesign showpiece. Aesthetic: "abyssal bioluminescent
 * telemetry" — a living-organism vital-signs monitor from the deep. The
 * agent's lifecycle is told as a LIVING LIFELINE: four life-stages strung
 * as glowing nodes along a vertical spine threaded by a breathing ECG
 * waveform (the agent's pulse).
 *
 * Design system is defined in `app/globals.css` under the `.abyss` scope
 * (all `--ab-*` tokens + keyframes), so this page is fully additive — the
 * legacy genesis-navy surfaces are untouched.
 *
 * Pure CSS motion (no client hooks) → this stays a server component. The
 * staggered "draw-in" reveal is driven by per-node `animation-delay`
 * inline styles consuming the `.ab-reveal` keyframe.
 */

type Status = "DONE" | "ACTIVE" | "LOCKED" | "COMING_SOON";

interface Stage {
  /** mono uppercase stage name shown on the node */
  name: string;
  /** the agent's developmental phase (serif) */
  phase: string;
  status: Status;
  /** the single headline stat */
  stat: string;
  /** route to open, or null for a future / unreachable stage */
  href: string | null;
  /** extra note for LOCKED stages */
  note?: string;
}

/**
 * Build the lifeline stages, gating the MOCK BET (Adult) stage behind the
 * L5-complete flag.
 *
 * While L5 is still learning the stage is LOCKED with a NULL href — so the
 * card renders un-wrapped (no <Link>) and can never navigate to the
 * not-yet-built /mock route. Once {@link readL5Complete} flips true the
 * stage becomes ACTIVE, gains its {@link MOCK_ROUTE} href, and drops the
 * "unlocks when L5 completes" note.
 *
 * Derived at render time (not module load) so the build-time-inlined
 * NEXT_PUBLIC_L5_COMPLETE value is honoured and tests can flip it.
 */
function buildStages(): readonly Stage[] {
  const l5Complete = readL5Complete();

  const mockBet: Stage = l5Complete
    ? {
        name: "MOCK BET",
        phase: "Adult",
        status: "ACTIVE",
        stat: "paper-trades live odds with no capital at risk",
        href: MOCK_ROUTE,
      }
    : {
        name: "MOCK BET",
        phase: "Adult",
        status: "LOCKED",
        stat: "paper-trades live odds with no capital at risk",
        // No href while locked → the card is rendered un-linked and cannot
        // 404 on the not-yet-built /mock route.
        href: null,
        note: "unlocks when L5 completes",
      };

  return [
    {
      name: "BACKTEST",
      phase: "Infancy",
      status: "DONE",
      stat: "optimal seed · Sharpe 0.649 · 81.5% win over 4925 markets",
      href: "/backtest",
    },
    {
      name: "L5 · LEARNING",
      phase: "Apprentice",
      status: "ACTIVE",
      stat: "learns to survive across deaths",
      href: "/survival",
    },
    mockBet,
    {
      name: "LIVEBET",
      phase: "Autonomous",
      status: "COMING_SOON",
      stat: "real capital · real permadeath",
      href: null,
    },
  ];
}

/** Status chip — visual language per lifecycle state. */
function StatusChip({ status }: { status: Status }): JSX.Element {
  const map: Record<
    Status,
    { label: string; cls: string; dot: string }
  > = {
    DONE: {
      label: "done",
      cls: "border-[var(--ab-moss)] text-[var(--ab-text)]",
      dot: "bg-[var(--ab-moss)]",
    },
    ACTIVE: {
      label: "active",
      cls: "border-[var(--ab-glow)] text-[var(--ab-glow)] ab-glow-text",
      dot: "bg-[var(--ab-glow)] ab-pulse-dot",
    },
    LOCKED: {
      label: "locked",
      cls: "border-[var(--ab-dim)]/50 text-[var(--ab-dim)]",
      dot: "bg-[var(--ab-dim)]",
    },
    COMING_SOON: {
      label: "coming soon",
      cls: "border-[var(--ab-dim)]/30 text-[var(--ab-dim)]/80",
      dot: "bg-[var(--ab-dim)]/60",
    },
  };
  const s = map[status];
  return (
    <span
      data-testid={`status-chip-${status}`}
      className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 font-mono text-[10px] uppercase tracking-[0.28em] ${s.cls}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${s.dot}`} aria-hidden />
      {s.label}
    </span>
  );
}

/** A small inline lock glyph for LOCKED stages. */
function LockGlyph(): JSX.Element {
  return (
    <svg
      width="11"
      height="11"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      aria-hidden
      className="inline-block"
    >
      <rect x="4" y="11" width="16" height="10" rx="2" />
      <path d="M8 11V7a4 4 0 0 1 8 0v4" />
    </svg>
  );
}

/** The breathing ECG waveform — the agent's heartbeat, in the glow color. */
function BreathWaveform({
  className = "",
}: {
  className?: string;
}): JSX.Element {
  // A flat baseline punctuated by a QRS-style spike, repeated — reads as a
  // cardiac monitor trace. Width 700 tiles cleanly under the dash-offset
  // animation defined on `.ab-breath-line`.
  const points =
    "0,30 90,30 110,30 120,12 130,48 142,8 154,30 240,30 330,30 350,30 360,14 370,46 382,6 394,30 480,30 560,30 580,30 590,12 600,48 612,8 624,30 700,30";
  return (
    <svg
      data-testid="breath-waveform"
      className={className}
      viewBox="0 0 700 60"
      preserveAspectRatio="none"
      role="img"
      aria-label="agent heartbeat waveform"
    >
      <polyline className="ab-breath-line" points={points} />
    </svg>
  );
}

export default function RoadmapPage(): JSX.Element {
  const stages = buildStages();
  return (
    <main id="main-content" className="abyss" data-testid="roadmap-page">
      <div className="mx-auto flex w-full max-w-3xl flex-col px-5 pb-24 pt-16 sm:px-8 sm:pt-24">
        {/* ---- HERO ------------------------------------------------- */}
        <header className="mb-20 flex flex-col items-center text-center">
          <p
            className="ab-hero-in mb-6 font-mono text-[10px] uppercase tracking-[0.5em] text-[var(--ab-dim)]"
            style={{ animationDelay: "60ms" }}
          >
            vital-signs monitor
          </p>
          <h1
            className="ab-hero-in font-display text-6xl leading-[0.92] text-[var(--ab-text)] sm:text-8xl"
            style={{ animationDelay: "140ms" }}
          >
            AUTOPOIESIS
          </h1>
          <p
            className="ab-hero-in mt-5 font-display text-xl italic text-[var(--ab-glow)] ab-glow-text sm:text-2xl"
            style={{ animationDelay: "300ms" }}
          >
            an agent that learns to survive
          </p>

          {/* The breath motif under the title. */}
          <div
            className="ab-hero-in mt-9 h-12 w-full max-w-md"
            style={{ animationDelay: "460ms" }}
          >
            <BreathWaveform className="h-full w-full" />
          </div>

          {/* Hub explainer cross-link — the "how it works" deep-dive. */}
          <Link
            href="/mechanism"
            data-testid="roadmap-mechanism-link"
            className="ab-hero-in mt-8 inline-flex w-fit items-center gap-2 rounded-sm font-mono text-[10px] uppercase tracking-[0.28em] text-[var(--ab-dim)] transition-colors hover:text-[var(--ab-glow)] focus:outline-none focus-visible:text-[var(--ab-glow)] focus-visible:ring-2 focus-visible:ring-[var(--ab-glow)]/70"
            style={{ animationDelay: "600ms" }}
          >
            how it works <span aria-hidden>▸</span> /mechanism
          </Link>

          {/* Paper-trail cross-link — contracts, runs, provenance. */}
          <Link
            href="/docs"
            data-testid="roadmap-docs-link"
            className="ab-hero-in mt-2 inline-flex w-fit items-center gap-2 rounded-sm font-mono text-[10px] uppercase tracking-[0.28em] text-[var(--ab-dim)] transition-colors hover:text-[var(--ab-glow)] focus:outline-none focus-visible:text-[var(--ab-glow)] focus-visible:ring-2 focus-visible:ring-[var(--ab-glow)]/70"
            style={{ animationDelay: "680ms" }}
          >
            verify everything <span aria-hidden>▸</span> /docs
          </Link>
        </header>

        {/* ---- LIFELINE -------------------------------------------- */}
        <section
          aria-label="agent lifecycle"
          className="relative pl-12 sm:pl-16"
        >
          {/* The glowing spine threading every node. */}
          <div
            aria-hidden
            className="ab-spine absolute bottom-6 left-[14px] top-2 w-[2px] rounded-full sm:left-[22px]"
          />

          <ol className="flex flex-col gap-12">
            {stages.map((stage, i) => {
              const isActive = stage.status === "ACTIVE";
              const isMuted =
                stage.status === "LOCKED" || stage.status === "COMING_SOON";
              // Only ACTIVE/DONE stages with a route are navigable. A LOCKED
              // stage is never wrapped in a <Link> even if it carries an href
              // — this closes the unconditional-Link gap so a locked card can
              // never navigate to an unbuilt route.
              const isLinked =
                stage.href !== null && stage.status !== "LOCKED";

              const card = (
                <article
                  data-testid={`stage-card-${i}`}
                  className={[
                    "group relative rounded-xl border px-5 py-5 transition-all duration-300 sm:px-7 sm:py-6",
                    isActive
                      ? "ab-pulse-node scale-[1.015] border-[var(--ab-glow)]/60 bg-[var(--ab-bg-2)]"
                      : "border-[var(--ab-moss)]/30 bg-[var(--ab-bg-2)]/60",
                    isMuted ? "opacity-60" : "",
                    stage.href ? "hover:border-[var(--ab-glow)]/50" : "",
                  ].join(" ")}
                >
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <h2
                      className={[
                        "font-mono uppercase tracking-[0.22em]",
                        isActive
                          ? "text-base text-[var(--ab-glow)] ab-glow-text sm:text-lg"
                          : "text-sm text-[var(--ab-text)]",
                      ].join(" ")}
                    >
                      {stage.name}
                    </h2>
                    <StatusChip status={stage.status} />
                  </div>

                  <p
                    className={[
                      "mt-1 font-display italic",
                      isActive
                        ? "text-3xl text-[var(--ab-text)] sm:text-4xl"
                        : "text-2xl text-[var(--ab-dim)]",
                    ].join(" ")}
                  >
                    {stage.phase}
                  </p>

                  <p
                    className={[
                      "mt-3 font-mono text-[11px] leading-relaxed tracking-wide sm:text-xs",
                      isActive ? "text-[var(--ab-text)]" : "text-[var(--ab-dim)]",
                    ].join(" ")}
                  >
                    {stage.stat}
                  </p>

                  {stage.note ? (
                    <p className="mt-3 flex w-fit items-center gap-2 font-mono text-[10px] uppercase tracking-[0.24em] text-[var(--ab-dim)]">
                      <LockGlyph />
                      {stage.note}
                    </p>
                  ) : null}

                  {stage.href ? (
                    <span className="mt-4 flex w-fit items-center gap-2 font-mono text-[10px] uppercase tracking-[0.28em] text-[var(--ab-glow)]/80 transition-colors group-hover:text-[var(--ab-glow)]">
                      open {stage.href} <span aria-hidden>▸</span>
                    </span>
                  ) : null}
                </article>
              );

              return (
                <li
                  key={stage.name}
                  data-testid={`stage-node-${i}`}
                  className="ab-reveal relative"
                  style={{ animationDelay: `${620 + i * 220}ms` }}
                >
                  {/* Node marker on the spine. */}
                  <span
                    aria-hidden
                    className={[
                      "absolute top-7 flex h-6 w-6 items-center justify-center rounded-full border-2",
                      "left-[-38px] sm:left-[-46px]",
                      isActive
                        ? "ab-pulse-node border-[var(--ab-glow)] bg-[var(--ab-bg)]"
                        : isMuted
                          ? "border-[var(--ab-dim)]/50 bg-[var(--ab-bg)]"
                          : "border-[var(--ab-moss)] bg-[var(--ab-bg)]",
                    ].join(" ")}
                  >
                    <span
                      className={[
                        "h-2 w-2 rounded-full",
                        isActive
                          ? "ab-pulse-dot bg-[var(--ab-glow)]"
                          : isMuted
                            ? "bg-[var(--ab-dim)]/70"
                            : "bg-[var(--ab-moss)]",
                      ].join(" ")}
                    />
                  </span>

                  {isLinked && stage.href !== null ? (
                    <Link
                      href={stage.href}
                      className="block rounded-xl focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ab-glow)]/70"
                    >
                      {card}
                    </Link>
                  ) : (
                    card
                  )}
                </li>
              );
            })}
          </ol>
        </section>

        {/* ---- FOOTER ---------------------------------------------- */}
        <footer
          className="ab-reveal mt-20 border-t border-[var(--ab-moss)]/25 pt-6 text-center font-mono text-[10px] uppercase tracking-[0.32em] text-[var(--ab-dim)]"
          style={{ animationDelay: `${620 + stages.length * 220}ms` }}
        >
          Robinhood Chain L2 · Polymarket tennis · permadeath sandbox
        </footer>
      </div>
    </main>
  );
}
