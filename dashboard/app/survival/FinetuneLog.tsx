"use client";

/**
 * FinetuneLog — the run-history / finetune-process section on /survival.
 *
 * One card per available journey version, data-driven from each fixture's
 * summary (the newer exporters self-disclose their realism rules via
 * `entry_price_floor` / `max_bet_pnl_usd`; the archived run1 snapshots predate
 * those keys, which is exactly how the cards tell v1 from v2). A fixed
 * narrative block explains WHAT changed between v1 and v2 and WHY — the
 * $0.0005-longshot lottery findings that motivated the rules.
 *
 * Clicking a card switches the page's journey toggle to that run, so a reader
 * can inspect any version's full curves. Abyssal-scoped, presentational; the
 * parent owns the selected-mode state.
 */

import type { JSX } from "react";

import type { SurvivalJourneyFixture } from "@/lib/load_survival_journey";
import type { SurvivalJourneyMode } from "@/lib/load_survival_journey.server";

const money = (n: number): string =>
  `${n < 0 ? "−" : ""}$${Math.abs(n).toLocaleString(undefined, {
    maximumFractionDigits: 0,
  })}`;

export interface FinetuneEntry {
  readonly mode: SurvivalJourneyMode;
  /** e.g. "v1 · Numerical" */
  readonly title: string;
  /** e.g. "run 1 · pre-rules" */
  readonly tag: string;
  readonly fixture: SurvivalJourneyFixture;
}

export interface FinetuneLogProps {
  readonly entries: readonly FinetuneEntry[];
  readonly activeMode: SurvivalJourneyMode;
  readonly onSelect: (mode: SurvivalJourneyMode) => void;
}

function rulesLabel(fixture: SurvivalJourneyFixture): string {
  const s = fixture.summary;
  const floor = s.entry_price_floor;
  const cap = s.max_bet_pnl_usd;
  if (floor == null && cap == null) {
    // Archived pre-rules run (keys absent) OR rules explicitly off.
    return "no realism rules · uncapped payouts";
  }
  const parts: string[] = [];
  // Realism v3 (run 3): side-correct payouts + EV-gated value decisions —
  // disclosed FIRST because they change what the money means.
  if (s.side_correct_pricing === true) parts.push("side-correct pricing");
  if (s.value_betting === true) parts.push("EV-gated");
  if (floor != null) parts.push(`entry price ≥ ${floor}`);
  if (cap != null) parts.push(`profit/bet ≤ $${cap}`);
  return parts.join(" · ");
}

export function FinetuneLog({
  entries,
  activeMode,
  onSelect,
}: FinetuneLogProps): JSX.Element | null {
  if (entries.length === 0) return null;
  return (
    <section
      data-testid="survival-finetune-log"
      className="ab-reveal mt-16 flex flex-col gap-5"
    >
      <h2 className="font-mono text-[10px] uppercase tracking-[0.32em] text-[var(--ab-dim)]">
        finetune log · run history
      </h2>

      {/* The WHY — the audit findings that motivated each generation of rules. */}
      <p className="max-w-3xl font-mono text-[11px] leading-relaxed text-[var(--ab-dim)]">
        After run 1 we audited the data: most of the headline came from two $5
        bets that hit extreme longshots at
        <span className="text-[var(--ab-text)]"> $0.0005 / $0.0055 </span>
        — bet size was held to $5 by our uniform conservative liquidity cap
        (the 24h-volume formula floors at $5 for resolved markets, so in
        practice every market gets $5), but the payout
        <span className="text-[var(--ab-text)]"> size×(1/price−1) </span>
        was unbounded (one bet &quot;won&quot; +$9,995 and pumped breath into
        the thousands, faking &quot;learned to survive&quot;). Run 2 introduced
        an entry-price floor and a per-bet profit cap, both enforced as hard
        invariants in the exporter.
      </p>
      <p
        data-testid="finetune-chapter-3"
        className="max-w-3xl font-mono text-[11px] leading-relaxed text-[var(--ab-dim)]"
      >
        After run 2 we audited the payout physics itself and found winning NO
        bets were paid at the YES leg&apos;s odds — always-favorite&apos;s
        <span className="text-[var(--ab-text)]"> +$8,451 </span>
        became
        <span className="text-[var(--ab-death)]"> −$661 </span>
        once each side paid its own price, and ~80–90% of our own learner
        P&amp;L had ridden the same artifact. Run 3 prices every leg
        correctly, floors the EFFECTIVE entry price, and replaces
        signal-betting with{" "}
        <span className="text-[var(--ab-text)]">
          EV-gated value betting
        </span>{" "}
        (market prior + signal tilt) — then re-picked the seed with an
        earnings-aligned sweep validated in the sequential season. The old
        seed rescored under the corrected physics:{" "}
        <span className="text-[var(--ab-text)]">$5.52</span>, statistically
        zero. The numbers got smaller, then real — and the exporter now
        recomputes every bet from first principles before an artifact can be
        written.
      </p>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {entries.map((e) => {
          const s = e.fixture.summary;
          const active = e.mode === activeMode;
          return (
            <button
              key={e.mode}
              type="button"
              data-testid={`finetune-card-${e.mode}`}
              data-active={active}
              onClick={() => onSelect(e.mode)}
              aria-label={`View the survival curves of ${e.title} (${e.tag})`}
              className={[
                "flex flex-col gap-2 rounded-xl border p-4 text-left transition-colors",
                "focus:outline-none focus-visible:ring-1 focus-visible:ring-[var(--ab-glow)]",
                active
                  ? "border-[var(--ab-glow)]/60 bg-[var(--ab-glow-soft)]"
                  : "border-[var(--ab-moss)]/30 bg-[var(--ab-bg-2)]/60 hover:bg-[var(--ab-moss)]/15",
              ].join(" ")}
            >
              <span className="flex items-baseline justify-between gap-2">
                <span
                  className={[
                    "font-display text-base leading-none",
                    active ? "text-[var(--ab-glow)] ab-glow-text" : "text-[var(--ab-text)]",
                  ].join(" ")}
                >
                  {e.title}
                </span>
                <span className="font-mono text-[8px] uppercase tracking-[0.2em] text-[var(--ab-dim)]">
                  {e.tag}
                </span>
              </span>
              <span
                data-testid={`finetune-rules-${e.mode}`}
                className="font-mono text-[9px] uppercase tracking-[0.14em] text-[var(--ab-dim)]"
              >
                {rulesLabel(e.fixture)}
              </span>
              <span className="mt-1 flex flex-wrap gap-x-4 gap-y-1 font-mono text-[10px] text-[var(--ab-text)]">
                <span>learner {money(s.learner_final_pnl)}</span>
                <span>ahead {money(s.learning_vs_static_delta)}</span>
                <span>
                  {s.lives} lives / {s.deaths} deaths
                </span>
                {typeof s.proposals_applied === "number" && s.proposals_applied > 0 ? (
                  <span>{s.proposals_applied} AI proposals applied</span>
                ) : null}
              </span>
              <span className="font-mono text-[9px] uppercase tracking-[0.18em] text-[var(--ab-dim)]/70">
                {active ? "showing ▴" : "view curves ▸"}
              </span>
            </button>
          );
        })}
      </div>

      {/* Open-ended coda — the log is a living document, not a closed book. */}
      <p
        data-testid="finetune-ongoing"
        className="max-w-3xl font-mono text-[11px] leading-relaxed text-[var(--ab-dim)]"
      >
        <span className="text-[var(--ab-glow)]">This log is open-ended.</span>{" "}
        Every audit so far made the numbers smaller and more honest — and the
        agent is still learning: better-calibrated signals, smarter sizing,
        and the live mock-bet season are next. Run 4 is a matter of{" "}
        <span className="text-[var(--ab-text)]">when</span>, not if.
      </p>
    </section>
  );
}

export default FinetuneLog;
