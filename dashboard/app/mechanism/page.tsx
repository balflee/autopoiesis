/**
 * /mechanism — the HUB-level project explainer (the "how it works" page).
 *
 * A content-rich, scannable walkthrough of the whole Autopoiesis project for
 * hackathon visitors: the arena (competition + chain), the data, the five
 * signal engines, the fusion brain, the lifecycle, the BREATH economy +
 * permadeath, the L5/L6 learning loops, the parameters we tuned, and the stack.
 *
 * This is a HUB explainer, NOT a numbered lifeline stage — so it uses a bespoke
 * abyss shell modelled on /roadmap (a centered hero with the breath-waveform
 * motif + a "◂ lifeline" back-link), threading its sections down a glowing
 * left spine like the lifeline. It reuses the `.abyss` design system in
 * `app/globals.css` (--ab-* tokens + ab-reveal/ab-hero-in/ab-breath-line),
 * matching its siblings /roadmap, /backtest, /survival, /mock.
 *
 * Pure CSS motion (no client hooks) → server component. The staggered reveal is
 * driven by per-section `animation-delay` inline styles consuming `.ab-reveal`.
 */

import Link from "next/link";
import type { JSX, ReactNode } from "react";

/* ------------------------------------------------------------------ */
/* The breath / heartbeat waveform — reused from the roadmap hero.     */
/* ------------------------------------------------------------------ */

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

/* ------------------------------------------------------------------ */
/* Section primitives                                                  */
/* ------------------------------------------------------------------ */

/** A section heading: small mono eyebrow (index + kicker) + serif title. */
function SectionHead({
  index,
  kicker,
  title,
}: {
  index: string;
  kicker: string;
  title: string;
}): JSX.Element {
  return (
    <div className="flex flex-col gap-1">
      <span className="font-mono text-[10px] uppercase tracking-[0.32em] text-[var(--ab-dim)]">
        {index} · {kicker}
      </span>
      <h2 className="font-display text-3xl italic text-[var(--ab-text)] sm:text-4xl">
        {title}
      </h2>
    </div>
  );
}

/**
 * A spine-threaded section: a node marker on the glowing left spine + the
 * section body. Mirrors the roadmap lifeline's node-on-spine framing, with a
 * staggered `.ab-reveal` entrance.
 */
function SpineSection({
  index,
  kicker,
  title,
  delayMs,
  testId,
  children,
  last = false,
}: {
  index: string;
  kicker: string;
  title: string;
  delayMs: number;
  testId: string;
  children: ReactNode;
  last?: boolean;
}): JSX.Element {
  return (
    <section
      data-testid={testId}
      className="ab-reveal relative"
      style={{ animationDelay: `${delayMs}ms` }}
    >
      {/* Node marker on the spine. */}
      <span
        aria-hidden
        className="absolute left-[-38px] top-1.5 flex h-6 w-6 items-center justify-center rounded-full border-2 border-[var(--ab-moss)] bg-[var(--ab-bg)] sm:left-[-46px]"
      >
        <span className="h-2 w-2 rounded-full bg-[var(--ab-moss)]" />
      </span>
      <SectionHead index={index} kicker={kicker} title={title} />
      <div className={last ? "mt-5" : "mt-5"}>{children}</div>
    </section>
  );
}

/** A glowing mono stat card — the headline numbers. */
function StatCard({
  value,
  label,
  tone = "glow",
}: {
  value: string;
  label: string;
  tone?: "glow" | "text";
}): JSX.Element {
  const valueCls =
    tone === "glow"
      ? "text-[var(--ab-glow)] ab-glow-text"
      : "text-[var(--ab-text)]";
  return (
    <div className="flex flex-col gap-1.5 rounded-xl border border-[var(--ab-moss)]/30 bg-[var(--ab-bg-2)]/60 p-4">
      <span
        className={`font-mono text-3xl leading-none tabular-nums sm:text-4xl ${valueCls}`}
      >
        {value}
      </span>
      <span className="font-mono text-[10px] uppercase tracking-[0.22em] text-[var(--ab-dim)]">
        {label}
      </span>
    </div>
  );
}

/** A bordered panel — the default surface for prose + lists. */
function Panel({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}): JSX.Element {
  return (
    <div
      className={`rounded-xl border border-[var(--ab-moss)]/30 bg-[var(--ab-bg-2)]/60 p-5 ${className}`}
    >
      {children}
    </div>
  );
}

/** Body prose styled for the abyss (mono, dim, relaxed). */
function Prose({ children }: { children: ReactNode }): JSX.Element {
  return (
    <p className="font-mono text-[12px] leading-relaxed text-[var(--ab-dim)]">
      {children}
    </p>
  );
}

/** Inline emphasis in the bright body color. */
function Hi({ children }: { children: ReactNode }): JSX.Element {
  return <span className="text-[var(--ab-text)]">{children}</span>;
}

/** Inline emphasis in the glow accent. */
function Glow({ children }: { children: ReactNode }): JSX.Element {
  return <span className="text-[var(--ab-glow)]">{children}</span>;
}

/** A small honest-caveat callout. */
function Caveat({ children }: { children: ReactNode }): JSX.Element {
  return (
    <div className="mt-4 rounded-xl border border-[var(--ab-glow)]/25 bg-[var(--ab-bg-3)]/50 p-4">
      <p className="font-mono text-[10px] uppercase tracking-[0.22em] text-[var(--ab-glow)]/90">
        honest note
      </p>
      <p className="mt-2 font-mono text-[11px] leading-relaxed text-[var(--ab-dim)]">
        {children}
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Engine list                                                         */
/* ------------------------------------------------------------------ */

const ENGINES: readonly {
  n: string;
  name: string;
  payload: string;
  body: string;
}[] = [
  {
    n: "1",
    name: "Market Momentum",
    payload: "CLOB price ledger",
    body: "Reads the intraday drift in the live order-book — where the money is moving as the match unfolds.",
  },
  {
    n: "2",
    name: "ELO / Ranking",
    payload: "elo / ranking gap",
    body: "Pre-match favorite strength from elo ratings — who the numbers say should win before a ball is struck.",
  },
  {
    n: "3",
    name: "Surface Form",
    payload: "surface win-rate",
    body: "Surface-specific form — clay, grass, hard. A player's win-rate is not one number; it is one per court.",
  },
  {
    n: "4",
    name: "Head-to-Head",
    payload: "h2h record",
    body: "Head-to-head history — some matchups defy the ratings because one player simply owns the other.",
  },
  {
    n: "5",
    name: "Rest & Recency",
    payload: "rest / recency",
    body: "Rest and recency — a fresh player against one three sets deep into a long week is a different bet.",
  },
];

/* ------------------------------------------------------------------ */
/* Lifecycle phases                                                    */
/* ------------------------------------------------------------------ */

const PHASES: readonly {
  phase: string;
  age: string;
  body: string;
  status: string;
}[] = [
  {
    phase: "backtest",
    age: "infancy",
    body: "Sweep the universe to find the best seed the agent could be born with.",
    status: "done",
  },
  {
    phase: "L5 survival",
    age: "apprentice",
    body: "Thrown into a survival season — it dies, respawns, and learns across deaths.",
    status: "active",
  },
  {
    phase: "mock bet",
    age: "adult",
    body: "Paper-trades live odds with no capital at risk — the real market, for free.",
    status: "next",
  },
  {
    phase: "livebet",
    age: "elder",
    body: "Real money, real permadeath. Coming soon.",
    status: "soon",
  },
];

/* ------------------------------------------------------------------ */
/* Page                                                                */
/* ------------------------------------------------------------------ */

export default function MechanismPage(): JSX.Element {
  return (
    <main id="main-content" className="abyss" data-testid="mechanism-route">
      <div className="mx-auto flex w-full max-w-3xl flex-col px-5 pb-24 pt-14 sm:px-8 sm:pt-20">
        {/* ---- HERO ------------------------------------------------- */}
        <header className="mb-16">
          <div className="mb-8 flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
            <Link
              href="/roadmap"
              data-testid="mechanism-back-link"
              aria-label="Back to the lifeline overview"
              className="rounded-sm font-mono text-[10px] uppercase tracking-[0.28em] text-[var(--ab-dim)] transition-colors hover:text-[var(--ab-glow)] focus:outline-none focus-visible:text-[var(--ab-glow)] focus-visible:ring-2 focus-visible:ring-[var(--ab-glow)]/70"
            >
              ◂ lifeline
            </Link>
            <span className="font-mono text-[10px] uppercase tracking-[0.28em] text-[var(--ab-dim)]">
              the whole picture
            </span>
          </div>

          <p
            className="ab-hero-in mb-4 font-mono text-[10px] uppercase tracking-[0.5em] text-[var(--ab-dim)]"
            style={{ animationDelay: "60ms" }}
          >
            how it works
          </p>
          <h1
            className="ab-hero-in font-display text-6xl leading-[0.9] text-[var(--ab-text)] sm:text-7xl"
            style={{ animationDelay: "140ms" }}
          >
            THE MECHANISM
          </h1>
          <p
            className="ab-hero-in mt-4 max-w-2xl font-display text-xl italic text-[var(--ab-glow)] ab-glow-text sm:text-2xl"
            style={{ animationDelay: "240ms" }}
          >
            a tennis-betting agent that learns, lives in phases, and can
            permanently die.
          </p>

          {/* The breath motif under the title — the agent's pulse. */}
          <div
            className="ab-hero-in mt-9 h-12 w-full max-w-md"
            style={{ animationDelay: "400ms" }}
          >
            <BreathWaveform className="h-full w-full" />
          </div>
        </header>

        {/* ---- SECTIONS (threaded down the spine) ------------------- */}
        <div className="relative pl-12 sm:pl-16">
          {/* The glowing spine threading every section node. */}
          <div
            aria-hidden
            className="ab-spine absolute bottom-10 left-[14px] top-2 w-[2px] rounded-full sm:left-[22px]"
          />

          <div className="flex flex-col gap-16">
            {/* §1 · THE ARENA --------------------------------------- */}
            <SpineSection
              index="01"
              kicker="the arena"
              title="An agent that lives on-chain"
              delayMs={120}
              testId="mechanism-arena"
            >
              <Panel>
                <Prose>
                  Built for the{" "}
                  <Glow>Arbitrum Open House London</Glow> hackathon,{" "}
                  <Hi>Autopoiesis</Hi> is an autonomous AI agent that lives on{" "}
                  <Hi>Robinhood Chain</Hi>, an Arbitrum L2. It bets on{" "}
                  <Hi>Polymarket tennis markets</Hi>, learns from realized
                  outcomes, matures through life-phases, and can{" "}
                  <Hi>permanently die</Hi> — permadeath that mints a Tombstone
                  NFT. It is not a model in a notebook; it is an organism with a
                  pulse, a bankroll, and a mortality.
                </Prose>
              </Panel>
            </SpineSection>

            {/* §2 · THE DATA ---------------------------------------- */}
            <SpineSection
              index="02"
              kicker="the data"
              title="Real markets, point-in-time"
              delayMs={200}
              testId="mechanism-data"
            >
              <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
                <StatCard value="7,494" label="match cassettes" />
                <StatCard value="4,925" label="resolved universe" />
                <StatCard value="65.7%" label="settled coverage" tone="text" />
              </div>
              <Panel className="mt-4">
                <Prose>
                  The price data is{" "}
                  <Hi>Polymarket tennis</Hi> — 7,494 per-match cassettes, each a
                  real CLOB intraday price ledger. Of those,{" "}
                  <Hi>4,925 (65.7%)</Hi> resolve to a settled outcome we can
                  grade against — that resolved set is the universe. Layered on
                  top is the{" "}
                  <Hi>Sackmann ATP/WTA dataset</Hi>: elo ratings, surface
                  win-rates, head-to-head, and rest/recency. Every signal is{" "}
                  <Glow>point-in-time correct</Glow> — computed at entry time,
                  with no lookahead.
                </Prose>
              </Panel>
            </SpineSection>

            {/* §3 · THE FIVE ENGINES -------------------------------- */}
            <SpineSection
              index="03"
              kicker="the senses"
              title="Five signal engines"
              delayMs={280}
              testId="mechanism-engines"
            >
              <Prose>
                Each market is read by five independent signal engines, then
                fused into one decision.
              </Prose>
              <ol className="mt-4 flex flex-col gap-3">
                {ENGINES.map((e) => (
                  <li
                    key={e.n}
                    className="flex gap-4 rounded-xl border border-[var(--ab-moss)]/30 bg-[var(--ab-bg-2)]/60 p-4"
                  >
                    <span className="font-mono text-lg tabular-nums text-[var(--ab-glow)] ab-glow-text">
                      {e.n}
                    </span>
                    <div className="flex flex-col gap-1">
                      <div className="flex flex-wrap items-baseline gap-2">
                        <span className="font-mono text-xs uppercase tracking-[0.18em] text-[var(--ab-text)]">
                          {e.name}
                        </span>
                        <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-[var(--ab-dim)]">
                          {e.payload}
                        </span>
                      </div>
                      <p className="font-mono text-[11px] leading-relaxed text-[var(--ab-dim)]">
                        {e.body}
                      </p>
                    </div>
                  </li>
                ))}
              </ol>
              <Caveat>
                Under the hood the DecisionEngine still keys three of these
                slots by names from an earlier prediction-markets prototype —{" "}
                <Hi>smart_money</Hi>, <Hi>sentiment_llm</Hi>,{" "}
                <Hi>crowd_volume</Hi> — but there is{" "}
                <Hi>no order-flow, social-sentiment, or betting-volume data</Hi>{" "}
                behind them. Each one computes a real tennis feature instead:
                surface win-rate, head-to-head record, and rest. The labels
                above are what actually runs.
              </Caveat>
            </SpineSection>

            {/* §4 · THE FUSION -------------------------------------- */}
            <SpineSection
              index="04"
              kicker="the brain"
              title="A 2-layer fusion engine"
              delayMs={360}
              testId="mechanism-fusion"
            >
              <Panel>
                <Prose>
                  A <Hi>2-layer decision engine</Hi> fuses the five signals via
                  tunable weights — two head weights (
                  <Hi>w_r / w_s</Hi>), three <Hi>alpha</Hi> weights, two{" "}
                  <Hi>beta</Hi> weights, and a <Hi>rho</Hi> mixing parameter —
                  then sizes the bet under four constraints:{" "}
                  <Hi>max-breath-risk</Hi>, <Hi>min-confidence</Hi>,{" "}
                  <Hi>min-bet</Hi>, and a <Hi>liquidity cap</Hi>. The best seed
                  found over the 4,925-market universe is selective and sharp:
                </Prose>
              </Panel>
              <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
                <StatCard value="0.649" label="per-bet Sharpe" />
                <StatCard value="81.5%" label="win rate" />
                <StatCard value="$853" label="summed PnL" tone="text" />
                <StatCard value="65" label="selective bets" tone="text" />
              </div>
            </SpineSection>

            {/* §5 · THE LIFECYCLE ----------------------------------- */}
            <SpineSection
              index="05"
              kicker="the lifecycle"
              title="It matures like a life"
              delayMs={440}
              testId="mechanism-lifecycle"
            >
              <Prose>
                The agent grows through four phases — infancy to elder — each a
                page of this dashboard.
              </Prose>
              <ol className="mt-4 flex flex-col gap-3">
                {PHASES.map((p, i) => (
                  <li
                    key={p.phase}
                    className="flex items-center gap-4 rounded-xl border border-[var(--ab-moss)]/30 bg-[var(--ab-bg-2)]/60 p-4"
                  >
                    <span className="font-mono text-[10px] tabular-nums text-[var(--ab-dim)]">
                      {i + 1}
                    </span>
                    <div className="flex flex-1 flex-col gap-0.5">
                      <div className="flex flex-wrap items-baseline justify-between gap-2">
                        <span className="font-mono text-xs uppercase tracking-[0.18em] text-[var(--ab-text)]">
                          {p.phase}
                        </span>
                        <span className="font-display text-lg italic text-[var(--ab-dim)]">
                          {p.age}
                        </span>
                      </div>
                      <p className="font-mono text-[11px] leading-relaxed text-[var(--ab-dim)]">
                        {p.body}
                      </p>
                    </div>
                    <span
                      className={[
                        "rounded-full border px-2.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.18em]",
                        p.status === "active"
                          ? "border-[var(--ab-glow)]/50 text-[var(--ab-glow)] ab-glow-text"
                          : p.status === "done"
                            ? "border-[var(--ab-moss)] text-[var(--ab-text)]"
                            : "border-[var(--ab-dim)]/40 text-[var(--ab-dim)]",
                      ].join(" ")}
                    >
                      {p.status}
                    </span>
                  </li>
                ))}
              </ol>
            </SpineSection>

            {/* §6 · THE BREATH ECONOMY & PERMADEATH ----------------- */}
            <SpineSection
              index="06"
              kicker="the stakes"
              title="BREATH & permadeath"
              delayMs={520}
              testId="mechanism-breath"
            >
              <Panel>
                {/* The breath motif — the life meter draining + refreshing. */}
                <div className="mb-4 h-10 w-full">
                  <BreathWaveform className="h-full w-full" />
                </div>
                <Prose>
                  The agent has <Hi>BREATH</Hi> — a life meter. Settlement
                  losses drain it; wins refresh it. When{" "}
                  <Glow>BREATH ≤ 0</Glow>, the agent <Hi>dies</Hi>: a{" "}
                  <Hi>Tombstone NFT</Hi> is minted, and it respawns{" "}
                  <Hi>fresh but keeps its learned weights</Hi>. So it does not
                  merely survive a single run — it learns to survive{" "}
                  <Glow>across deaths</Glow>.
                </Prose>
                <Caveat>
                  In the survival simulation, BREATH is driven purely by
                  settlement PnL — call it{" "}
                  <Hi>settlement-loss survival</Hi>. No funding rates, no gas
                  drain; the only thing that can kill the agent is losing bets.
                </Caveat>
              </Panel>
            </SpineSection>

            {/* §7 · THE LEARNING (L5 + L6) -------------------------- */}
            <SpineSection
              index="07"
              kicker="the learning"
              title="L5 + L6: reflect, learn, optimize"
              delayMs={600}
              testId="mechanism-learning"
            >
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <Panel>
                  <span className="font-mono text-[10px] uppercase tracking-[0.22em] text-[var(--ab-glow)]/90">
                    L5 · settlement self-learning
                  </span>
                  <p className="mt-2 font-mono text-[12px] leading-relaxed text-[var(--ab-dim)]">
                    After each bet <Hi>settles</Hi>, a{" "}
                    <Hi>WeightUpdater</Hi> nudges the fusion weights — an EMA —
                    toward what actually worked. The agent tunes itself, one
                    realized outcome at a time.
                  </p>
                </Panel>
                <Panel>
                  <span className="font-mono text-[10px] uppercase tracking-[0.22em] text-[var(--ab-glow)]/90">
                    L6 · reflection-driven optimization
                  </span>
                  <p className="mt-2 font-mono text-[12px] leading-relaxed text-[var(--ab-dim)]">
                    A real LLM — <Hi>Gemini 3.5 Flash</Hi>, with{" "}
                    <Hi>MiniMax</Hi> as fallback — writes natural-language{" "}
                    <Hi>reflections</Hi> on recent performance. A{" "}
                    <Hi>StrategyAdvisor</Hi> turns those into concrete{" "}
                    <Hi>weight-change proposals</Hi> that flow through an
                    approval queue and get applied.
                  </p>
                </Panel>
              </div>
              <p className="mt-4 text-center font-display text-xl italic text-[var(--ab-glow)] ab-glow-text">
                reflect → learn → optimize
              </p>
            </SpineSection>

            {/* §8 · THE PARAMETERS WE TUNED ------------------------- */}
            <SpineSection
              index="08"
              kicker="the experiments"
              title="The parameters we tuned"
              delayMs={680}
              testId="mechanism-params"
            >
              <ul className="flex flex-col gap-3">
                <li className="rounded-xl border border-[var(--ab-moss)]/30 bg-[var(--ab-bg-2)]/60 p-4">
                  <span className="font-mono text-xs uppercase tracking-[0.18em] text-[var(--ab-text)]">
                    the seed config
                  </span>
                  <p className="mt-1.5 font-mono text-[11px] leading-relaxed text-[var(--ab-dim)]">
                    The six fusion weights (w_r/w_s, three alphas, two betas,
                    rho) plus the four sizing knobs — the agent's birth policy.
                  </p>
                </li>
                <li className="rounded-xl border border-[var(--ab-moss)]/30 bg-[var(--ab-bg-2)]/60 p-4">
                  <span className="font-mono text-xs uppercase tracking-[0.18em] text-[var(--ab-text)]">
                    the survival calibration
                  </span>
                  <p className="mt-1.5 font-mono text-[11px] leading-relaxed text-[var(--ab-dim)]">
                    A deliberately <Hi>fragile</Hi> seed, a{" "}
                    <Hi>loss multiplier</Hi>, and a low{" "}
                    <Hi>initial breath</Hi> — so deaths actually occur and
                    learning has something to rescue.
                  </p>
                </li>
                <li className="rounded-xl border border-[var(--ab-moss)]/30 bg-[var(--ab-bg-2)]/60 p-4">
                  <span className="font-mono text-xs uppercase tracking-[0.18em] text-[var(--ab-text)]">
                    the reflection / advisor cadences
                  </span>
                  <p className="mt-1.5 font-mono text-[11px] leading-relaxed text-[var(--ab-dim)]">
                    How often the LLM reflects and how often the advisor
                    proposes — the rhythm of the L6 loop.
                  </p>
                </li>
              </ul>
              <Prose>
                <span className="mt-4 block">
                  Different parameter sets shape the agent's{" "}
                  <Hi>personality</Hi> — a cautious-survivor versus an
                  aggressive-earner — a story the dashboard can show across
                  runs.
                </span>
              </Prose>
            </SpineSection>

            {/* §9 · THE STACK --------------------------------------- */}
            <SpineSection
              index="09"
              kicker="the stack"
              title="What it's built on"
              delayMs={760}
              testId="mechanism-stack"
              last
            >
              <div className="flex flex-wrap gap-2.5">
                {[
                  "Robinhood Chain (Arbitrum L2)",
                  "Polymarket",
                  "Sackmann tennis data",
                  "Gemini 3.5 Flash + MiniMax",
                  "Next.js dashboard",
                ].map((chip) => (
                  <span
                    key={chip}
                    className="rounded-full border border-[var(--ab-moss)]/40 bg-[var(--ab-bg-2)]/60 px-3.5 py-1.5 font-mono text-[11px] tracking-wide text-[var(--ab-text)]"
                  >
                    {chip}
                  </span>
                ))}
              </div>
            </SpineSection>
          </div>
        </div>

        {/* ---- FOOTER ---------------------------------------------- */}
        <footer className="mt-20 flex flex-wrap items-baseline justify-between gap-3 border-t border-[var(--ab-moss)]/25 pt-6 font-mono text-[10px] uppercase tracking-[0.28em] text-[var(--ab-dim)]">
          <span>Robinhood Chain L2 · Polymarket tennis · permadeath sandbox</span>
          <Link
            href="/roadmap"
            aria-label="Back to the lifeline overview"
            className="rounded-sm text-[var(--ab-glow)]/80 transition-colors hover:text-[var(--ab-glow)] focus:outline-none focus-visible:text-[var(--ab-glow)] focus-visible:ring-2 focus-visible:ring-[var(--ab-glow)]/70"
          >
            ◂ back to the lifeline
          </Link>
        </footer>
      </div>
    </main>
  );
}
