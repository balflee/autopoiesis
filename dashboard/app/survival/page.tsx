/**
 * /survival — Phase D/E showpiece: the L5 LEARNING story (E1).
 *
 * The agent is born with the optimal seed (the /backtest page), then thrown
 * into a survival season: it lives, its breath drains, it DIES (permadeath →
 * Tombstone), respawns, and slowly learns to survive. This page tells that arc
 * on the REAL `survival_journey.json` run, surfaced through the validated
 * loader + adapter in `lib/load_survival_journey.ts` (codex M5: an ADAPTER to
 * the generic chart primitives, NOT a cast into the Phase-1 fixture, NOT the
 * live WS store).
 *
 * Server component: it reads the large, gitignored artifact from disk via the
 * server companion loader and hands the validated fixture to the interactive
 * client view. If the artifact is absent (a fresh checkout that hasn't run the
 * export), the page degrades to a "not yet generated" note rather than crashing
 * the build.
 *
 * Aesthetic: the `.abyss` design system in `app/globals.css`, matching the
 * roadmap landing + the /backtest page.
 */

import type { JSX, ReactNode } from "react";

import { loadSurvivalJourneyOrNull } from "@/lib/load_survival_journey.server";
import { type SurvivalJourneyFixture } from "@/lib/load_survival_journey";
import { readL5Complete } from "@/lib/l5_gate";
import { STAGE_META } from "@/lib/lifeline";
import {
  BackLinkDim,
  LifelineFooter,
  NextLink,
  StageShell,
} from "@/components/lifeline/StageShell";

import SurvivalJourneyShell from "./SurvivalJourneyShell";

// The artifact is generated; read it fresh at request time (never inline a
// 4 MB JSON into a static chunk).
export const dynamic = "force-dynamic";

/**
 * Thin wrapper over the shared {@link StageShell} pinning the survival stage
 * metadata. Kept as a local helper so both the "missing artifact" empty-state
 * (no footer) and the populated body (footer) render through the SAME shell.
 */
function Shell({
  children,
  footer,
}: {
  children: ReactNode;
  footer?: ReactNode;
}): JSX.Element {
  return (
    <StageShell meta={STAGE_META.survival} footer={footer}>
      {children}
    </StageShell>
  );
}

export default async function SurvivalRoute(): Promise<JSX.Element> {
  let fixture: SurvivalJourneyFixture | null = null;
  let aiFixture: SurvivalJourneyFixture | null = null;
  let aiGeminiFixture: SurvivalJourneyFixture | null = null;
  let run1Fixture: SurvivalJourneyFixture | null = null;
  let aiRun1Fixture: SurvivalJourneyFixture | null = null;
  let run2Fixture: SurvivalJourneyFixture | null = null;
  let aiRun2Fixture: SurvivalJourneyFixture | null = null;
  let aiGeminiRun2Fixture: SurvivalJourneyFixture | null = null;
  let loadError: string | null = null;
  try {
    // Numerical run is the required primary artifact; the AI run, the
    // Gemini-only provider leg, AND the archived run1 (pre-realism-rules) +
    // run2 (pre-value-physics) snapshots (the finetune-log exhibits) are all
    // OPTIONAL — read via the graceful loader so a missing file degrades the
    // toggle/log rather than the page. A malformed file STILL throws (caught
    // below).
    [
      fixture,
      aiFixture,
      aiGeminiFixture,
      run1Fixture,
      aiRun1Fixture,
      run2Fixture,
      aiRun2Fixture,
      aiGeminiRun2Fixture,
    ] = await Promise.all([
      loadSurvivalJourneyOrNull({ mode: "numerical" }),
      loadSurvivalJourneyOrNull({ mode: "ai" }),
      loadSurvivalJourneyOrNull({ mode: "ai_gemini" }),
      loadSurvivalJourneyOrNull({ mode: "numerical_run1" }),
      loadSurvivalJourneyOrNull({ mode: "ai_run1" }),
      loadSurvivalJourneyOrNull({ mode: "numerical_run2" }),
      loadSurvivalJourneyOrNull({ mode: "ai_run2" }),
      loadSurvivalJourneyOrNull({ mode: "ai_gemini_run2" }),
    ]);
  } catch (err) {
    loadError = err instanceof Error ? err.message : String(err);
  }

  if (!fixture) {
    return (
      <Shell>
        <section
          data-testid="survival-missing"
          className="rounded-xl border border-[var(--ab-moss)]/30 bg-[var(--ab-bg-2)]/60 p-6"
        >
          <p className="font-mono text-[11px] uppercase tracking-[0.22em] text-[var(--ab-death)]">
            survival run not generated
          </p>
          <p className="mt-3 font-mono text-[11px] leading-relaxed text-[var(--ab-dim)]">
            The survival-journey artifact has not been exported yet. Run the L5
            export to produce{" "}
            <span className="text-[var(--ab-text)]">
              public/backtest/survival_journey.json
            </span>
            , then reload.
          </p>
          {loadError ? (
            <p className="mt-3 font-mono text-[10px] leading-relaxed text-[var(--ab-dim)]/70">
              {loadError}
            </p>
          ) : null}
        </section>
      </Shell>
    );
  }

  const s = fixture.summary;
  // The forward lifeline points at /mock — a stage SEALED until L5 completes.
  // Gate the "next" link on the same fail-closed flag the /mock route + roadmap
  // node use, so we never offer a link into a locked (404-able) stage.
  const l5Complete = readL5Complete();

  return (
    <Shell
      footer={
        <LifelineFooter
          note={<>L5 · survival season · {s.total_steps} learner bets</>}
          nav={
            <div className="flex items-baseline gap-5">
              <BackLinkDim
                href={STAGE_META.backtest.href}
                ariaLabel="Back to the seed: the backtest stage"
              >
                ◂ back to the seed
              </BackLinkDim>
              {l5Complete && (
                <NextLink
                  href={STAGE_META.mock.href}
                  testId="survival-next-link"
                  ariaLabel="Next stage: live mock-bet"
                >
                  next · live mock-bet ▸
                </NextLink>
              )}
            </div>
          }
        />
      }
    >
      {/* Mode toggle + mode-reactive headline/narrative + interactive body +
          finetune log all live in the client shell so the Numerical/AI/v1 switch
          re-feeds them. */}
      <SurvivalJourneyShell
        numerical={fixture}
        ai={aiFixture}
        aiGemini={aiGeminiFixture}
        numericalRun1={run1Fixture}
        aiRun1={aiRun1Fixture}
        numericalRun2={run2Fixture}
        aiRun2={aiRun2Fixture}
        aiGeminiRun2={aiGeminiRun2Fixture}
      />
    </Shell>
  );
}
