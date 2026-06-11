"use client";

/**
 * SurvivalJourneyView — the interactive body of /survival (E2: THE STAR).
 *
 * Client component holding the playback scrub state. It AUTO-PLAYS on mount —
 * the season scrubs itself, telling the story without a click — and wires the
 * survival ADAPTER (`lib/load_survival_journey.ts`) to:
 *   - the HEADLINE learner-vs-static P&L overlay (the learner curve tearing
 *     away from the frozen baseline),
 *   - the fusion-weight evolution chart (α₀ / ρ climbing as it learns which
 *     engines to trust),
 *   - a BREATH / vitals gauge draining toward each death,
 *   - the current-bet card + the five engine signals (read from the journey
 *     prop, NEVER the live WS store — codex M5),
 *   - the TOMBSTONE graveyard of the six dead lives,
 *   - the playback scrubber with life-boundary + death markers.
 *
 * Everything reads the already-validated `SurvivalJourneyFixture` handed down
 * from the server page. It never reads the file itself and never touches the
 * live WS store.
 */

import { useEffect, useMemo, useState, type JSX } from "react";

import BacktestScrubber from "@/components/BacktestScrubber";
import PnLBaselineChart from "@/components/PnLBaselineChart";
import WeightEvolutionChart from "@/components/WeightEvolutionChart";
import {
  adaptPnlViewModel,
  adaptScrubberViewModel,
  adaptWeightViewModel,
  stepAt,
  survivingLife,
  tombstones,
  vitalsForStep,
  type SurvivalJourneyFixture,
} from "@/lib/load_survival_journey";

import SurvivalMatchCard from "./SurvivalMatchCard";
import SurvivalVitalsWidget from "./SurvivalVitalsWidget";
import TombstoneStrip from "./TombstoneStrip";

const money = (n: number): string =>
  `${n < 0 ? "−" : ""}$${Math.abs(n).toLocaleString(undefined, {
    maximumFractionDigits: 0,
  })}`;

/** Playback speed (steps/second) — 842 bets play out in ~12s on auto-run. */
const SURVIVAL_PLAYBACK_SPEED = 70;

export function SurvivalJourneyView({
  fixture,
}: {
  readonly fixture: SurvivalJourneyFixture;
}): JSX.Element {
  const [stepIndex, setStepIndex] = useState(0);
  // Auto-play on mount: the season tells itself. A flag (not `true` in initial
  // useState) so SSR renders the paused frame and hydration kicks playback —
  // and so we only auto-start ONCE (a manual pause then sticks).
  const [playing, setPlaying] = useState(false);
  useEffect(() => {
    setPlaying(true);
  }, []);

  const weightVm = useMemo(() => adaptWeightViewModel(fixture), [fixture]);
  const pnlVm = useMemo(() => adaptPnlViewModel(fixture), [fixture]);
  const scrubberVm = useMemo(() => adaptScrubberViewModel(fixture), [fixture]);
  const tombs = useMemo(() => tombstones(fixture), [fixture]);
  const hero = useMemo(() => survivingLife(fixture), [fixture]);

  const clampedIdx = Math.max(0, Math.min(fixture.steps.length - 1, stepIndex));
  const step = stepAt(fixture, clampedIdx);
  const vitals = useMemo(
    () => vitalsForStep(fixture, clampedIdx),
    [fixture, clampedIdx],
  );
  const lifeIdx = scrubberVm.lifeIdxByStep[clampedIdx] ?? 0;

  // The weight chart's first→last deltas — the learning, in one line.
  const weightNote = useMemo(() => {
    const first = fixture.steps[0];
    const last = fixture.steps[fixture.steps.length - 1];
    if (!first || !last) return undefined;
    const a0 = `α₀ ${first.weights.alpha_0.toFixed(2)} → ${last.weights.alpha_0.toFixed(2)}`;
    const rho = `ρ ${first.weights.rho.toFixed(2)} → ${last.weights.rho.toFixed(2)}`;
    return `it learned which engines to trust · ${a0} · ${rho}`;
  }, [fixture]);

  const weightVmWithNote = useMemo(
    () => ({ ...weightVm, note: weightNote }),
    [weightVm, weightNote],
  );

  const statusLabel = step
    ? `life ${lifeIdx} / ${scrubberVm.totalLives - 1} · breath ${step.breath.toFixed(0)} · cum ${money(
        step.cum_pnl,
      )}`
    : "";

  const delta = fixture.summary.learning_vs_static_delta;

  return (
    <div data-testid="survival-journey-view" className="flex flex-col gap-10">
      {/* ── HEADLINE: learner vs static — the divergence that is the story. ─ */}
      <section
        data-testid="survival-pnl-section"
        className="flex flex-col gap-3"
      >
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="font-mono text-[10px] uppercase tracking-[0.32em] text-[var(--ab-dim)]">
            cumulative p&amp;l · learner vs frozen seed
          </h2>
          <p
            data-testid="survival-headline-delta"
            className="font-mono text-[10px] uppercase tracking-[0.22em] text-[var(--ab-glow)] ab-glow-text"
          >
            learner {money(fixture.summary.learner_final_pnl)} · static{" "}
            {money(fixture.summary.static_final_pnl)} ·{" "}
            <span className="text-[var(--ab-text)]">+{money(delta)} apart</span>
          </p>
        </div>
        <PnLBaselineChart viewModel={pnlVm} activeIndex={clampedIdx} variant="abyss" />
      </section>

      {/* ── VITALS + CURRENT BET — the live-feeling per-step telemetry. ──── */}
      <section
        data-testid="survival-telemetry-section"
        className="grid grid-cols-1 gap-6 lg:grid-cols-2"
      >
        <SurvivalVitalsWidget vitals={vitals} totalLives={scrubberVm.totalLives} />
        <SurvivalMatchCard
          step={step}
          stepIndex={clampedIdx}
          totalSteps={fixture.steps.length}
        />
      </section>

      {/* ── WEIGHT EVOLUTION — what learning actually moved. ─────────────── */}
      <section data-testid="survival-weight-section" className="flex flex-col gap-3">
        <h2 className="font-mono text-[10px] uppercase tracking-[0.32em] text-[var(--ab-dim)]">
          fusion-weight evolution
        </h2>
        <WeightEvolutionChart viewModel={weightVmWithNote} activeIndex={clampedIdx} variant="abyss" />
      </section>

      {/* ── GRAVEYARD — the six dead lives, lit as the scrubber crosses. ─── */}
      <section data-testid="survival-graveyard-section" className="flex flex-col gap-3">
        <TombstoneStrip tombstones={tombs} activeIndex={clampedIdx} />
        {hero ? (
          <p
            data-testid="survival-survivor-note"
            className="max-w-3xl font-mono text-[11px] leading-relaxed text-[var(--ab-dim)]"
          >
            then life{" "}
            <span className="text-[var(--ab-glow)] ab-glow-text">{hero.idx}</span>{" "}
            held its breath across{" "}
            <span className="text-[var(--ab-text)]">{hero.bets}</span> bets,
            banked{" "}
            <span className="text-[var(--ab-text)]">{money(hero.pnl)}</span>, and{" "}
            <span className="text-[var(--ab-glow)]">survived</span>.
          </p>
        ) : null}
      </section>

      {/* ── SCRUBBER — walk the season; life boundaries + deaths marked. ─── */}
      <section data-testid="survival-scrubber-section">
        <BacktestScrubber
          viewModel={scrubberVm}
          activeIndex={clampedIdx}
          onChange={setStepIndex}
          statusLabel={statusLabel}
          playing={playing}
          onTogglePlay={() => setPlaying((p) => !p)}
          playbackSpeed={SURVIVAL_PLAYBACK_SPEED}
          variant="abyss"
        />
      </section>
    </div>
  );
}

export default SurvivalJourneyView;
