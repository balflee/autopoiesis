"use client";

/**
 * PhaseEvolution — the tabbed "how each stage changed the world" island on
 * the /mechanism (how-it-works) page. The page itself is a server component
 * (pure CSS motion); this is the one client island because the tab switch
 * needs ``useState``.
 *
 * Each tab is one chronological stage of the project's evolution — what
 * changed, why we changed it, and the HONEST result (the project's whole
 * credibility rests on never dressing a failure as a win). Content is static
 * (no data fetch) so the component is trivially testable.
 */

import { useState, type JSX } from "react";

interface Phase {
  readonly id: string;
  readonly tab: string;
  readonly kicker: string;
  readonly title: string;
  readonly status: { readonly label: string; readonly tone: "done" | "live" | "open" };
  /** "what changed" — the mechanism added at this stage. */
  readonly changed: readonly string[];
  /** "why" — one line of motivation. */
  readonly why: string;
  /** "what we found" — the honest result. */
  readonly found: string;
}

const PHASES: readonly Phase[] = [
  {
    id: "p1",
    tab: "Ⅰ · backtest",
    kicker: "phase 1",
    title: "One life, one season",
    status: { label: "published", tone: "done" },
    changed: [
      "Five signal engines → a 2-layer fusion brain → Kelly sizing, all bounded by BREATH.",
      "An EMA weight-learner adapts the six fusion weights after every settled bet.",
      "The agent lives ONCE through a chronological tennis season — the chapter-1 record.",
    ],
    why: "Establish the agent and the physics, and measure whether weight-learning alone produces a profitable, survivable strategy.",
    found:
      "The learning gradient is DEATH-BLIND — per-bet PnL credit only, no breath or death term. Survival was never in the objective. That single fact is the seed of everything that follows.",
  },
  {
    id: "p2",
    tab: "Ⅱ · reincarnation",
    kicker: "phase 2",
    title: "Die. Remember. Restart at bet #1.",
    status: { label: "published", tone: "done" },
    changed: [
      "Death sends the agent back to the season's first market, carrying its weights + the EMA learner's aggregates — but never the outcomes.",
      "Permadeath economics: a dead life's profit is FORFEIT (scored $0). Only a life that survives the whole season keeps its earnings.",
      "The loop repeats until one life survives or a cap; then a learning-frozen cold-start holdout checks generalization.",
    ],
    why: "Test whether carrying experience across deaths — the core autopoiesis bet — lets the agent learn to survive.",
    found:
      "The control died at settled bet 826 nineteen times to the cent (~52% of the season). Published as the PREDICTED plateau — a death-blind learner cannot route around death.",
  },
  {
    id: "a56",
    tab: "goal & prayer",
    kicker: "additions A5 · A6",
    title: "The agent sees the finish line — and prays",
    status: { label: "published", tone: "done" },
    changed: [
      "A5: at each death the agent is shown the goal, where it died, and its personal best across all its lives.",
      "A6: it states one dying wish — what parameter or information it most wants next life. Recorded for the gods, never granted mid-experiment, never carried forward.",
    ],
    why: "Give the agent a sense of progress, and a channel to voice what it lacks — without leaking anything into the next life.",
    found:
      "Its wishes — breath-aware sizing, skip low-confidence bets, the true win probability — matched the designers' roadmap item for item. The prayer log now drives what it gets next.",
  },
  {
    id: "a7",
    tab: "tribute",
    kicker: "addition A7",
    title: "The gods sell breath at the deathbed",
    status: { label: "published", tone: "done" },
    changed: [
      "A dying agent may OFFER money to the gods for a fresh lungful — minimum $500 (~30% grant), rising to ~99% at $2,000.",
      "The offering is kept win or lose — the gods have an agenda, and it is money.",
    ],
    why: "The agent dies HOLDING ~$2,470 it can't keep and can't spend on the one thing it needs — breath. So the world grew a market for it.",
    found:
      "Every life bought revivals and STILL died: the gods collected $50,719 across 20 lives while the agent scored $0. Reviving into your own pending-loss pipeline is a money pit — $0.009 earned per $1 paid.",
  },
  {
    id: "a9",
    tab: "emergence kit",
    kicker: "addition A9",
    title: "The actuator finally gets a gauge",
    status: { label: "inconclusive", tone: "open" },
    changed: [
      "A storm percept: a breath-space regime signal (EMA of the real breath delta, 48h wall-clock decay) the agent can finally see.",
      "Conditional gate levers (γ, γ2) on a rebirth-advisable genome — and a counterfactual ledger in the death window that turns the 1-bit died/survived signal into dense causal evidence.",
      "A pre-registered falsification leg (timestamp-shuffled control season) + an evaluable gate: under 3 productive death reviews ⇒ the verdict reads INCONCLUSIVE, never a vacuous pass.",
    ],
    why: "The only entity that could move a regime lever — the rebirth advisor — saw only blended aggregates, so any regime move could only be a pretrained prior. The kit gives it a gauge.",
    found:
      "G1 (full kit) pushed death ~19pp deeper after a single advisor decision — but still died every life ($0). By our own gate the result is INCONCLUSIVE: 1 of 3 productive reviews, the depth gain confounded with a plain sizing cut, inert out of sample. Audited by an adversarial agent panel before publishing.",
  },
  {
    id: "a10",
    tab: "divine tithe",
    kicker: "addition A10",
    title: "The gods charge rent for existence",
    status: { label: "in flight", tone: "live" },
    changed: [
      "Every 20 markets the agent pays $20 from its bankroll — or, when broke, loses 5 breath instead (cash-preferred).",
      "A do-nothing agent stops earning, runs out of cash, and bleeds out — closing the loophole where a choked agent survives by freezing its breath and never betting.",
    ],
    why: "The falsification leg 'survived' only because a near-dead agent got choked into a zero-metabolism coma — NO_BET cost nothing. Existence should cost breath.",
    found:
      "By design it self-targets the income-less: agents that die rich always pay cash (their regime death is unchanged), while the coma agent runs out and dies → its advisor finally fires → the falsification leg becomes evaluable. Parameters are being tuned against the live re-run.",
  },
];

function statusClasses(tone: Phase["status"]["tone"]): string {
  if (tone === "done") return "border-[var(--ab-glow)]/50 text-[var(--ab-glow)]";
  if (tone === "open") return "border-[var(--ab-death)]/50 text-[var(--ab-death)]";
  return "border-[var(--ab-moss)]/50 text-[var(--ab-text)]";
}

export default function PhaseEvolution(): JSX.Element {
  const [active, setActive] = useState<string>(PHASES[0]!.id);
  const phase = PHASES.find((p) => p.id === active) ?? PHASES[0]!;

  return (
    <div data-testid="phase-evolution" className="flex flex-col gap-5">
      {/* Tab strip — chronological stages. */}
      <div
        role="tablist"
        aria-label="project evolution stages"
        className="flex flex-wrap gap-2"
      >
        {PHASES.map((p) => {
          const selected = p.id === active;
          return (
            <button
              key={p.id}
              role="tab"
              type="button"
              aria-selected={selected}
              data-testid={`phase-tab-${p.id}`}
              data-active={selected}
              onClick={() => setActive(p.id)}
              className={[
                "rounded-full border px-3.5 py-1.5 font-mono text-[10px] uppercase tracking-[0.18em] transition-colors",
                selected
                  ? "border-[var(--ab-glow)]/60 text-[var(--ab-glow)]"
                  : "border-[var(--ab-moss)]/30 text-[var(--ab-dim)] hover:text-[var(--ab-text)]",
              ].join(" ")}
            >
              {p.tab}
            </button>
          );
        })}
      </div>

      {/* Active phase panel. */}
      <div
        role="tabpanel"
        data-testid={`phase-panel-${phase.id}`}
        className="rounded-xl border border-[var(--ab-moss)]/30 bg-[var(--ab-bg-2)]/60 p-5"
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="font-mono text-[10px] uppercase tracking-[0.28em] text-[var(--ab-dim)]">
            {phase.kicker}
          </span>
          <span
            className={[
              "rounded-full border px-2.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.2em]",
              statusClasses(phase.status.tone),
            ].join(" ")}
          >
            {phase.status.label}
          </span>
        </div>
        <h3 className="mt-2 font-display text-2xl leading-tight text-[var(--ab-text)]">
          {phase.title}
        </h3>

        <p className="mt-4 font-mono text-[10px] uppercase tracking-[0.22em] text-[var(--ab-glow)]/80">
          what changed
        </p>
        <ul className="mt-2 flex flex-col gap-2">
          {phase.changed.map((c, i) => (
            <li
              key={i}
              className="flex gap-2 font-mono text-[11px] leading-relaxed text-[var(--ab-dim)]"
            >
              <span aria-hidden className="text-[var(--ab-glow)]">
                ▸
              </span>
              <span>{c}</span>
            </li>
          ))}
        </ul>

        <p className="mt-4 font-mono text-[11px] leading-relaxed text-[var(--ab-dim)]">
          <span className="font-mono text-[10px] uppercase tracking-[0.22em] text-[var(--ab-text)]">
            why ·{" "}
          </span>
          {phase.why}
        </p>

        <div className="mt-4 rounded-lg border border-[var(--ab-glow)]/25 bg-[var(--ab-bg-3)]/50 p-3.5">
          <p className="font-mono text-[10px] uppercase tracking-[0.22em] text-[var(--ab-glow)]/90">
            what we found (honest)
          </p>
          <p className="mt-2 font-mono text-[11px] leading-relaxed text-[var(--ab-dim)]">
            {phase.found}
          </p>
        </div>
      </div>
    </div>
  );
}
