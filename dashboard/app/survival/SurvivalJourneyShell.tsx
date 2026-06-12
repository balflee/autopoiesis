"use client";

/**
 * SurvivalJourneyShell — client wrapper owning the Numerical/AI toggle state (E-toggle).
 *
 * The server page (`page.tsx`) reads BOTH journey artifacts from disk and hands
 * them down (the AI one possibly `null`). This wrapper owns the selected-mode
 * state, renders the mode-reactive headline + narrative, the toggle, and re-feeds
 * {@link SurvivalJourneyView} with the selected fixture. `SurvivalJourneyView` is
 * fully driven by its `fixture` prop (auto-play, scrub state, all adapters keyed
 * on `[fixture]`), so swapping the prop cleanly restarts the story for the new
 * run — a `key={mode}` remounts it so playback resets from step 0.
 *
 * Default = Numerical. If the AI journey is unavailable the toggle still
 * renders the AI option, disabled with a "pending" hint.
 */

import { useState, type JSX } from "react";

import {
  bestLife,
  type SurvivalJourneyFixture,
} from "@/lib/load_survival_journey";
import type { SurvivalJourneyMode } from "@/lib/load_survival_journey.server";

import FinetuneLog, { type FinetuneEntry } from "./FinetuneLog";
import SurvivalJourneyView from "./SurvivalJourneyView";
import SurvivalModeToggle from "./SurvivalModeToggle";

const money = (n: number): string =>
  `${n < 0 ? "−" : ""}$${Math.abs(n).toLocaleString(undefined, {
    maximumFractionDigits: 0,
  })}`;

function HeadlineStat({
  value,
  label,
  tone = "glow",
}: {
  value: string;
  label: string;
  tone?: "glow" | "text" | "death";
}): JSX.Element {
  const cls =
    tone === "glow"
      ? "text-[var(--ab-glow)] ab-glow-text"
      : tone === "death"
        ? "text-[var(--ab-death)]"
        : "text-[var(--ab-text)]";
  return (
    <div className="flex flex-col gap-1">
      <span className={`font-display text-4xl leading-none sm:text-5xl ${cls}`}>{value}</span>
      <span className="font-mono text-[10px] uppercase tracking-[0.24em] text-[var(--ab-dim)]">
        {label}
      </span>
    </div>
  );
}

export interface SurvivalJourneyShellProps {
  readonly numerical: SurvivalJourneyFixture;
  /** The AI run, or `null` when its artifact has not been generated. */
  readonly ai: SurvivalJourneyFixture | null;
  /** Gemini-only provider-comparison leg (same physics); optional. */
  readonly aiGemini?: SurvivalJourneyFixture | null;
  /** Archived pre-realism-rules (v1) snapshots (finetune-log exhibits); optional. */
  readonly numericalRun1?: SurvivalJourneyFixture | null;
  readonly aiRun1?: SurvivalJourneyFixture | null;
  /** Archived pre-value-physics (v2) snapshots (finetune-log exhibits); optional. */
  readonly numericalRun2?: SurvivalJourneyFixture | null;
  readonly aiRun2?: SurvivalJourneyFixture | null;
  readonly aiGeminiRun2?: SurvivalJourneyFixture | null;
}

export function SurvivalJourneyShell({
  numerical,
  ai,
  aiGemini = null,
  numericalRun1 = null,
  aiRun1 = null,
  numericalRun2 = null,
  aiRun2 = null,
  aiGeminiRun2 = null,
}: SurvivalJourneyShellProps): JSX.Element {
  const [mode, setMode] = useState<SurvivalJourneyMode>("numerical");
  const aiAvailable = ai !== null;

  // Mode → fixture; any unavailable selection falls back to numerical.
  const byMode: Record<SurvivalJourneyMode, SurvivalJourneyFixture | null> = {
    numerical,
    ai,
    ai_gemini: aiGemini,
    numerical_run1: numericalRun1,
    ai_run1: aiRun1,
    numerical_run2: numericalRun2,
    ai_run2: aiRun2,
    ai_gemini_run2: aiGeminiRun2,
  };
  const fixture = byMode[mode] ?? numerical;
  const s = fixture.summary;
  const best = bestLife(fixture);

  // Finetune-log cards: archived v1 first (the "before"), archived v2 next,
  // then the current v3 runs — chronological reading order. Only available
  // runs become cards.
  const finetuneEntries: FinetuneEntry[] = [];
  if (numericalRun1) {
    finetuneEntries.push({
      mode: "numerical_run1", title: "v1 · Numerical", tag: "run 1 · pre-rules", fixture: numericalRun1,
    });
  }
  if (aiRun1) {
    finetuneEntries.push({
      mode: "ai_run1", title: "v1 · AI", tag: "run 1 · pre-rules", fixture: aiRun1,
    });
  }
  if (numericalRun2) {
    finetuneEntries.push({
      mode: "numerical_run2",
      title: "v2 · Numerical",
      tag: "run 2 · realism rules",
      fixture: numericalRun2,
    });
  }
  if (aiRun2) {
    finetuneEntries.push({
      mode: "ai_run2",
      title: "v2 · AI · MiniMax",
      tag: "run 2 · realism rules",
      fixture: aiRun2,
    });
  }
  if (aiGeminiRun2) {
    finetuneEntries.push({
      mode: "ai_gemini_run2",
      title: "v2 · AI · Gemini",
      tag: "run 2 · provider comparison",
      fixture: aiGeminiRun2,
    });
  }
  finetuneEntries.push({
    mode: "numerical",
    title: "v3 · Numerical",
    tag: "run 3 · value physics",
    fixture: numerical,
  });
  if (ai) {
    finetuneEntries.push({
      mode: "ai", title: "v3 · AI · MiniMax", tag: "run 3 · value physics", fixture: ai,
    });
  }
  if (aiGemini) {
    finetuneEntries.push({
      mode: "ai_gemini",
      title: "v3 · AI · Gemini",
      tag: "run 3 · value physics",
      fixture: aiGemini,
    });
  }

  return (
    <>
      {/* ── Toggle: Numerical vs AI — the run currently telling the story. ── */}
      <section
        data-testid="survival-mode-section"
        className="ab-reveal mb-10 flex flex-col gap-3"
      >
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="font-mono text-[10px] uppercase tracking-[0.32em] text-[var(--ab-dim)]">
            learning mode
          </h2>
          {/* The agent is a living, still-training system — run 3 is the
              CURRENT chapter, not the last one. */}
          <span
            data-testid="survival-still-learning"
            className="inline-flex items-center gap-1.5 rounded-full border border-[var(--ab-glow)]/50 px-2.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.18em] text-[var(--ab-glow)]"
          >
            <span aria-hidden className="h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--ab-glow)]" />
            still learning · run 3 is not the end
          </span>
        </div>
        <SurvivalModeToggle
          mode={mode}
          onChange={setMode}
          aiAvailable={aiAvailable}
          aiGeminiAvailable={aiGemini !== null}
          numericalRun1Available={numericalRun1 !== null}
          aiRun1Available={aiRun1 !== null}
          numericalRun2Available={numericalRun2 !== null}
          aiRun2Available={aiRun2 !== null}
          aiGeminiRun2Available={aiGeminiRun2 !== null}
        />
      </section>

      {/* Headline telemetry — the learner-vs-static divergence is the story. */}
      <section
        data-testid="survival-headline"
        className="ab-reveal mb-14 grid grid-cols-2 gap-6 sm:grid-cols-4"
      >
        <HeadlineStat value={`${s.deaths}`} label="deaths → survival" tone="death" />
        <HeadlineStat value={money(s.learner_final_pnl)} label="learner P&L" />
        <HeadlineStat value={money(s.static_final_pnl)} label="static seed P&L" tone="text" />
        <HeadlineStat
          value={`+${money(s.learning_vs_static_delta)}`}
          label="learner ahead"
        />
      </section>

      {/* Narrative line. */}
      <section className="ab-reveal mb-10">
        <p className="max-w-3xl font-mono text-[11px] leading-relaxed text-[var(--ab-dim)]">
          One seed. {s.lives} lives. The agent began with the backtest&apos;s
          optimal policy and was dropped into a live-shaped survival season —
          every loss drains its breath, and when breath hits zero it{" "}
          <span className="text-[var(--ab-death)]">dies for good</span>, mints a
          Tombstone, and respawns. It died{" "}
          <span className="text-[var(--ab-text)]">{s.deaths}</span> times before
          life&nbsp;
          <span className="text-[var(--ab-glow)] ab-glow-text">{s.best_life}</span>{" "}
          {best ? `survived ${best.bets} bets` : "survived"} and pulled{" "}
          <span className="text-[var(--ab-text)]">
            {money(s.learning_vs_static_delta)}
          </span>{" "}
          ahead of the static seed running the same markets.
        </p>
      </section>

      {/* The interactive body — remounted on mode switch so playback restarts. */}
      <SurvivalJourneyView key={mode} fixture={fixture} />

      {/* Finetune log — run-history cards; clicking one re-feeds the view above. */}
      <FinetuneLog
        entries={finetuneEntries}
        activeMode={mode}
        onSelect={setMode}
      />
    </>
  );
}

export default SurvivalJourneyShell;
