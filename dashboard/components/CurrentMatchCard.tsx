"use client";

/**
 * CurrentMatchCard — T-D-008 (sprint_7 Day 5-6).
 *
 * Right-rail panel that surfaces the live tick's match metadata so the
 * audience can see which fixture the trained model is currently grading.
 *
 * PRD acceptance: player A vs B + surface + tour level + market price + edge.
 *
 * The synthetic `<initial>` tick (tick 0) shows a "training run boot" card
 * instead — the journey's first row is the uniform-prior weights before any
 * gradient step.
 */

import { type JSX, useMemo } from "react";

import { ColorTokens } from "@/lib/colorTokens";
import {
  matchForTick,
  type TrainingJourneyFixture,
} from "@/lib/load_training_journey";

const SURFACE_ACCENT: Record<string, string> = {
  Hard: "#7DD3FC", // sky
  Clay: ColorTokens.AMBER,
  Grass: ColorTokens.WIN,
  Carpet: ColorTokens.INK_MUTED,
};

export interface CurrentMatchCardProps {
  readonly fixture: TrainingJourneyFixture;
  readonly tickIndex: number;
}

export function CurrentMatchCard({ fixture, tickIndex }: CurrentMatchCardProps): JSX.Element {
  const clampedIdx = Math.max(0, Math.min(fixture.ticks.length - 1, Math.trunc(tickIndex)));
  const tick = fixture.ticks[clampedIdx]!;
  const match = matchForTick(fixture, tick);

  /** Compose the trained model's predicted prob from current α blend.
   *  Same formula as `dashboard/scripts/build_training_journey.py:trained_p_hat`
   *  — keep them in sync. The card is purely informational; this preview
   *  illustrates "what the trained model would say at this snapshot in time."
   */
  const pHat = useMemo(() => {
    if (!match) return null;
    return 0.5 + (match.market_yes_price - 0.5) * tick.alpha_1;
  }, [match, tick.alpha_1]);

  if (!match) {
    return (
      <section
        data-testid="current-match-card"
        data-empty="true"
        role="region"
        aria-label="Current match — initial uniform prior"
        className="flex h-full w-full flex-col gap-3 rounded-lg border border-genesis-ink-muted/30 bg-genesis-bg p-5 text-genesis-ink"
      >
        <header className="flex items-baseline justify-between font-mono text-[11px] uppercase tracking-[0.22em] text-genesis-ink-muted">
          <span>current match</span>
          <span>tick 0</span>
        </header>
        <div className="flex flex-1 flex-col items-start justify-center gap-3">
          <p className="font-mono text-[12px] uppercase tracking-[0.18em] text-genesis-amber">
            training run · boot
          </p>
          <p className="text-genesis-ink-muted">
            Uniform prior — every signal weighted equally. Drag the scrubber to
            walk the trained model forward one match at a time.
          </p>
        </div>
        <footer className="font-mono text-[10px] uppercase tracking-[0.18em] text-genesis-ink-muted">
          {fixture.n_matches} matches · {fixture.n_epochs} epochs
        </footer>
      </section>
    );
  }

  const market = match.market_yes_price;
  const edgePct = match.edge_pct; // (0.5 - market) × 100
  const surfaceColor = SURFACE_ACCENT[match.surface] ?? ColorTokens.INK_MUTED;
  const outcomeLabel = match.outcome === 1 ? "Player A" : "Player B";
  const outcomeColor = match.outcome === 1 ? ColorTokens.WIN : ColorTokens.LOSS;

  return (
    <section
      data-testid="current-match-card"
      data-match-id={tick.match_id}
      role="region"
      aria-label={`Current match ${tick.match_id}: ${match.player_a} vs ${match.player_b} on ${match.surface}`}
      className="flex h-full w-full flex-col gap-4 rounded-lg border border-genesis-ink-muted/30 bg-genesis-bg p-5 text-genesis-ink"
    >
      <header className="flex items-baseline justify-between font-mono text-[11px] uppercase tracking-[0.22em] text-genesis-ink-muted">
        <span>current match</span>
        <span data-testid="current-match-id" className="text-genesis-ink">
          {tick.match_id}
        </span>
      </header>

      {/* Players */}
      <div className="flex flex-col gap-2">
        <div className="flex items-baseline justify-between">
          <span
            data-testid="current-match-player-a"
            className="font-mono text-base text-genesis-ink"
          >
            Player {match.player_a}
          </span>
          <span
            className="font-mono text-[10px] uppercase tracking-[0.18em] text-genesis-ink-muted"
          >
            A
          </span>
        </div>
        <div className="flex items-center gap-3 font-mono text-[10px] uppercase tracking-[0.22em] text-genesis-ink-muted">
          <span className="h-px flex-1 bg-genesis-ink-muted/30" />
          vs
          <span className="h-px flex-1 bg-genesis-ink-muted/30" />
        </div>
        <div className="flex items-baseline justify-between">
          <span
            data-testid="current-match-player-b"
            className="font-mono text-base text-genesis-ink"
          >
            Player {match.player_b}
          </span>
          <span
            className="font-mono text-[10px] uppercase tracking-[0.18em] text-genesis-ink-muted"
          >
            B
          </span>
        </div>
      </div>

      {/* Surface / tour / best-of */}
      <ul
        data-testid="current-match-metadata"
        className="grid grid-cols-2 gap-2 font-mono text-[11px] uppercase tracking-[0.18em] text-genesis-ink-muted"
      >
        <li className="flex items-center gap-2">
          <span
            aria-hidden
            className="inline-block h-2 w-2 rounded-full"
            style={{ backgroundColor: surfaceColor }}
          />
          <span className="text-genesis-ink">{match.surface}</span>
        </li>
        <li>
          <span data-testid="current-match-tour-level" className="text-genesis-ink">
            {match.tour_level}
          </span>
        </li>
        <li>
          best of <span className="text-genesis-ink">{match.best_of}</span>
        </li>
        <li>
          settled{" "}
          <span className="text-genesis-ink" style={{ color: outcomeColor }}>
            {outcomeLabel}
          </span>
        </li>
      </ul>

      {/* Market + edge */}
      <div
        data-testid="current-match-market"
        className="grid grid-cols-2 gap-3 border-t border-genesis-ink-muted/20 pt-3"
      >
        <div className="flex flex-col">
          <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-genesis-ink-muted">
            market (Yes / A)
          </span>
          <span
            data-testid="current-match-market-price"
            className="font-mono text-xl text-genesis-ink"
          >
            {market.toFixed(3)}
          </span>
        </div>
        <div className="flex flex-col">
          <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-genesis-ink-muted">
            edge (vs 0.5)
          </span>
          <span
            data-testid="current-match-edge"
            className="font-mono text-xl"
            style={{
              color: edgePct >= 0 ? ColorTokens.WIN : ColorTokens.LOSS,
            }}
          >
            {edgePct >= 0 ? "+" : ""}
            {edgePct.toFixed(2)}%
          </span>
        </div>
      </div>

      {/* Trained model preview */}
      {pHat !== null && (
        <div
          data-testid="current-match-trained-preview"
          className="flex items-baseline justify-between border-t border-genesis-ink-muted/20 pt-3 font-mono text-[10px] uppercase tracking-[0.18em] text-genesis-ink-muted"
        >
          <span>trained p̂(A)</span>
          <span className="text-genesis-ink">
            {pHat.toFixed(3)}
            <span
              className="ml-2"
              style={{
                color: pHat - market >= 0 ? ColorTokens.WIN : ColorTokens.LOSS,
              }}
            >
              Δ {((pHat - market) * 100).toFixed(2)}%
            </span>
          </span>
        </div>
      )}

      <footer className="font-mono text-[10px] uppercase tracking-[0.18em] text-genesis-ink-muted">
        ts {formatTs(match.asof_ts)}
      </footer>
    </section>
  );
}

function formatTs(s: string): string {
  // Display the date portion; full ISO is too noisy in the card.
  const idx = s.indexOf("T");
  return idx > 0 ? s.slice(0, idx) : s;
}

export default CurrentMatchCard;
