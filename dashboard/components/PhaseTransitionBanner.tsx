"use client";

/**
 * PhaseTransitionBanner — sticky top banner that announces the latest
 * phase transition. PRD §8 spec: cool-to-warm blue gradient on
 * Phase 1 → Phase 2 (the "apprentice awakens" beat).
 *
 * The banner is dismissible — clicking the X clears the `phaseTransition`
 * slice in the store. It also auto-dismisses after 30 s so it does not
 * occlude the dashboard for the rest of the run.
 *
 * SSR-safe: the auto-dismiss timer is gated by useEffect.
 */

import { useEffect, type JSX } from "react";

import { useWsStore } from "@/lib/wsStore";
import type { AgentPhase } from "@/lib/types";

const AUTO_DISMISS_MS = 30_000;

const PHASE_PRETTY: Record<AgentPhase, string> = {
  PHASE_1_INFANCY: "Phase 1 · Infancy",
  PHASE_2_APPRENTICE: "Phase 2 · Apprenticeship",
  PHASE_3_MASTER: "Phase 3 · Mastery",
  PHASE_4_TERMINAL: "Phase 4 · Terminal Lucidity",
};

/**
 * Cool→warm blue gradient palette. We avoid pulling in dynamic class
 * names so Tailwind JIT does not need to be re-configured; the
 * gradient lives in inline style.
 */
function gradientFor(from: AgentPhase, to: AgentPhase): string {
  // PRD §8 demo beat — P1 → P2 is the canonical cool→warm. Other
  // transitions use a neutral cool→amber for visual continuity.
  if (from === "PHASE_1_INFANCY" && to === "PHASE_2_APPRENTICE") {
    return "linear-gradient(90deg, #143A6B 0%, #1E5BA5 40%, #FFB703 100%)";
  }
  if (to === "PHASE_3_MASTER") {
    return "linear-gradient(90deg, #143A6B 0%, #1E5BA5 40%, #06D6A0 100%)";
  }
  if (to === "PHASE_4_TERMINAL") {
    return "linear-gradient(90deg, #1E5BA5 0%, #6F1D2A 60%, #E63946 100%)";
  }
  return "linear-gradient(90deg, #143A6B 0%, #1E5BA5 60%, #9FB0C4 100%)";
}

export function PhaseTransitionBanner(): JSX.Element | null {
  const transition = useWsStore((s) => s.phaseTransition);
  const dismiss = useWsStore((s) => s.dismissPhaseTransition);

  useEffect(() => {
    if (!transition) return;
    if (typeof window === "undefined") return;
    const t = window.setTimeout(() => {
      dismiss();
    }, AUTO_DISMISS_MS);
    return () => window.clearTimeout(t);
  }, [transition, dismiss]);

  if (!transition) return null;

  const { from, to, reason } = transition.payload;

  return (
    <aside
      data-testid="phase-transition-banner"
      data-from={from}
      data-to={to}
      role="status"
      aria-live="polite"
      className="sticky top-0 z-30 w-full"
    >
      <div
        className="flex items-center justify-between gap-4 px-4 py-3 text-genesis-ink shadow-lg"
        style={{ background: gradientFor(from, to) }}
      >
        <div className="flex flex-col gap-0.5">
          <span
            data-testid="phase-transition-banner-headline"
            className="font-mono text-sm uppercase tracking-[0.18em]"
          >
            {PHASE_PRETTY[from]} → {PHASE_PRETTY[to]}
          </span>
          {reason && (
            <span
              data-testid="phase-transition-banner-reason"
              className="font-mono text-xs text-genesis-ink/85"
            >
              {reason}
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={dismiss}
          data-testid="phase-transition-banner-dismiss"
          aria-label="Dismiss phase transition banner"
          className="rounded border border-genesis-ink/40 px-2 py-0.5 font-mono text-xs text-genesis-ink hover:bg-genesis-ink/10"
        >
          ×
        </button>
      </div>
    </aside>
  );
}

export default PhaseTransitionBanner;
