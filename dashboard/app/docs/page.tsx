/**
 * /docs — the PAPER-TRAIL page (contracts, runs, data provenance, fine print).
 *
 * Everything a judge or auditor needs to verify the project: the five deployed
 * contracts on both chains (with explorer links), the honest note on why the
 * TombstoneNFT has no mints yet, the realism rules the simulation enforces,
 * every survival run on the record (including the fluke-inflated v1 runs), the
 * data provenance, and the repo + stack.
 *
 * Same bespoke abyss shell as /mechanism (hero + glowing left spine), reusing
 * the `.abyss` design system in `app/globals.css`. Pure CSS motion → server
 * component.
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
/* Section primitives (mirrors /mechanism)                             */
/* ------------------------------------------------------------------ */

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

function SpineSection({
  index,
  kicker,
  title,
  delayMs,
  testId,
  children,
}: {
  index: string;
  kicker: string;
  title: string;
  delayMs: number;
  testId: string;
  children: ReactNode;
}): JSX.Element {
  return (
    <section
      data-testid={testId}
      className="ab-reveal relative"
      style={{ animationDelay: `${delayMs}ms` }}
    >
      <span
        aria-hidden
        className="absolute left-[-38px] top-1.5 flex h-6 w-6 items-center justify-center rounded-full border-2 border-[var(--ab-moss)] bg-[var(--ab-bg)] sm:left-[-46px]"
      >
        <span className="h-2 w-2 rounded-full bg-[var(--ab-moss)]" />
      </span>
      <SectionHead index={index} kicker={kicker} title={title} />
      <div className="mt-5">{children}</div>
    </section>
  );
}

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

function Prose({ children }: { children: ReactNode }): JSX.Element {
  return (
    <p className="font-mono text-[12px] leading-relaxed text-[var(--ab-dim)]">
      {children}
    </p>
  );
}

function Hi({ children }: { children: ReactNode }): JSX.Element {
  return <span className="text-[var(--ab-text)]">{children}</span>;
}

function Glow({ children }: { children: ReactNode }): JSX.Element {
  return <span className="text-[var(--ab-glow)]">{children}</span>;
}

/** A small honest-caveat callout (same as /mechanism). */
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

/** An external link in the abyss idiom. */
function Ext({
  href,
  children,
}: {
  href: string;
  children: ReactNode;
}): JSX.Element {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="rounded-sm font-mono text-[10px] uppercase tracking-[0.18em] text-[var(--ab-glow)] underline decoration-[var(--ab-glow)]/40 underline-offset-4 transition-colors hover:decoration-[var(--ab-glow)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ab-glow)]/70"
    >
      {children}
    </a>
  );
}

/* ------------------------------------------------------------------ */
/* Contract registry — addresses identical on both chains.             */
/* ------------------------------------------------------------------ */

const RH_EXPLORER = "https://explorer.testnet.chain.robinhood.com/address/";
const ARB_EXPLORER = "https://sepolia.arbiscan.io/address/";

const CONTRACTS: readonly {
  name: string;
  abi: string;
  address: string;
  role: string;
}[] = [
  {
    name: "TombstoneNFT",
    abi: "v0.2.0",
    address: "0xDE6178D892AA9F80f748a399f07B588b08Faec2f",
    role: "The permadeath monument — minted when the agent dies, with a fully on-chain SVG tokenURI. One token per death, forever.",
  },
  {
    name: "AgentLifecycle",
    abi: "v0.3.0",
    address: "0x125929f6451e5e5Fa9C64b498646793CaF5b4128",
    role: "The birth/death state machine. The only contract allowed to write the DecisionLog and trigger a Tombstone mint.",
  },
  {
    name: "DecisionLog",
    abi: "v0.1.0",
    address: "0x3e58BE777F8fe7F1B81dfBdFA716295D0EF89818",
    role: "Append-only log of the agent's betting decisions. Only AgentLifecycle may write.",
  },
  {
    name: "EnergyController",
    abi: "v0.4.0",
    address: "0xeb504449195b0491F52b455650056f0763A54525",
    role: "BREATH accounting — the life-meter that settlement losses drain and wins refresh.",
  },
  {
    name: "PhaseManager",
    abi: "v0.3.0",
    address: "0x20e07db0169E35553a66608736161f433d8E44E0",
    role: "Gates the lifecycle phases (backtest → survival → mock-bet → live) and emits the Phase-3 role-renunciation events.",
  },
];

/* ------------------------------------------------------------------ */
/* Run history — every survival run, on the record.                    */
/* ------------------------------------------------------------------ */

const RUNS: readonly {
  run: string;
  rules: string;
  pnl: string;
  lives: string;
  applied: string;
}[] = [
  {
    run: "v1 · Numerical",
    rules: "pre-rules · uncapped",
    pnl: "$11,879",
    lives: "7 / 6",
    applied: "—",
  },
  {
    run: "v1 · AI (MiniMax)",
    rules: "pre-rules · uncapped",
    pnl: "$17,469",
    lives: "6 / 5",
    applied: "130",
  },
  {
    run: "v2 · Numerical",
    rules: "floor 0.05 · cap $100",
    pnl: "$1,668",
    lives: "8 / 7",
    applied: "—",
  },
  {
    run: "v2 · AI (MiniMax)",
    rules: "floor 0.05 · cap $100",
    pnl: "$2,757",
    lives: "10 / 9",
    applied: "126",
  },
  {
    run: "v2 · AI (Gemini)",
    rules: "floor 0.05 · cap $100",
    pnl: "$2,510",
    lives: "10 / 9",
    applied: "411",
  },
];

/* ------------------------------------------------------------------ */
/* Page                                                                */
/* ------------------------------------------------------------------ */

export default function DocsPage(): JSX.Element {
  return (
    <main id="main-content" className="abyss" data-testid="docs-route">
      <div className="mx-auto flex w-full max-w-3xl flex-col px-5 pb-24 pt-14 sm:px-8 sm:pt-20">
        {/* ---- HERO ------------------------------------------------- */}
        <header className="mb-16">
          <div className="mb-8 flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
            <Link
              href="/roadmap"
              data-testid="docs-back-link"
              aria-label="Back to the lifeline overview"
              className="rounded-sm font-mono text-[10px] uppercase tracking-[0.28em] text-[var(--ab-dim)] transition-colors hover:text-[var(--ab-glow)] focus:outline-none focus-visible:text-[var(--ab-glow)] focus-visible:ring-2 focus-visible:ring-[var(--ab-glow)]/70"
            >
              ◂ lifeline
            </Link>
            <span className="font-mono text-[10px] uppercase tracking-[0.28em] text-[var(--ab-dim)]">
              the paper trail
            </span>
          </div>

          <p
            className="ab-hero-in mb-4 font-mono text-[10px] uppercase tracking-[0.5em] text-[var(--ab-dim)]"
            style={{ animationDelay: "60ms" }}
          >
            verify everything
          </p>
          <h1
            className="ab-hero-in font-display text-6xl leading-[0.9] text-[var(--ab-text)] sm:text-7xl"
            style={{ animationDelay: "140ms" }}
          >
            DOCS
          </h1>
          <p
            className="ab-hero-in mt-4 max-w-2xl font-display text-xl italic text-[var(--ab-glow)] ab-glow-text sm:text-2xl"
            style={{ animationDelay: "240ms" }}
          >
            contracts, runs, data — and the honest fine print.
          </p>

          <div
            className="ab-hero-in mt-9 h-12 w-full max-w-md"
            style={{ animationDelay: "400ms" }}
          >
            <BreathWaveform className="h-full w-full" />
          </div>
        </header>

        {/* ---- SECTIONS (threaded down the spine) ------------------- */}
        <div className="relative pl-12 sm:pl-16">
          <div
            aria-hidden
            className="ab-spine absolute bottom-10 left-[14px] top-2 w-[2px] rounded-full sm:left-[22px]"
          />

          <div className="flex flex-col gap-16">
            {/* §1 · DEPLOYED CONTRACTS ------------------------------ */}
            <SpineSection
              index="01"
              kicker="the contracts"
              title="Five contracts, two chains"
              delayMs={120}
              testId="docs-contracts"
            >
              <Panel>
                <Prose>
                  All five contracts are deployed at{" "}
                  <Hi>identical addresses</Hi> on{" "}
                  <Glow>Robinhood Chain testnet</Glow> (chainId{" "}
                  <Hi>46630</Hi>, deploy block 60897767) and{" "}
                  <Glow>Arbitrum Sepolia</Glow> (chainId <Hi>421614</Hi>,
                  deploy block 10917212). Every address below links to both
                  explorers — verify, don&apos;t trust.
                </Prose>
              </Panel>
              <ul className="mt-4 flex flex-col gap-3">
                {CONTRACTS.map((c) => (
                  <li
                    key={c.name}
                    className="flex flex-col gap-2 rounded-xl border border-[var(--ab-moss)]/30 bg-[var(--ab-bg-2)]/60 p-4"
                  >
                    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                      <span className="font-mono text-xs uppercase tracking-[0.18em] text-[var(--ab-text)]">
                        {c.name}
                      </span>
                      <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-[var(--ab-dim)]">
                        abi {c.abi}
                      </span>
                    </div>
                    <code className="break-all font-mono text-[11px] text-[var(--ab-glow)]">
                      {c.address}
                    </code>
                    <p className="font-mono text-[11px] leading-relaxed text-[var(--ab-dim)]">
                      {c.role}
                    </p>
                    <div className="flex flex-wrap gap-4">
                      <Ext href={`${RH_EXPLORER}${c.address}`}>
                        robinhood chain ↗
                      </Ext>
                      <Ext href={`${ARB_EXPLORER}${c.address}`}>
                        arbitrum sepolia ↗
                      </Ext>
                    </div>
                  </li>
                ))}
              </ul>
            </SpineSection>

            {/* §2 · WHY ZERO TOMBSTONES ----------------------------- */}
            <SpineSection
              index="02"
              kicker="the fine print"
              title="Why zero Tombstones (so far)"
              delayMs={200}
              testId="docs-tombstone-note"
            >
              <Panel>
                <Prose>
                  The deaths on this dashboard happen in the{" "}
                  <Hi>backtest / survival simulation</Hi>, which replays 4,925
                  historical markets in minutes against an{" "}
                  <Hi>in-memory chain adapter</Hi> — simulated deaths do{" "}
                  <Glow>not</Glow> mint NFTs, so the TombstoneNFT contract
                  intentionally has <Hi>no mints yet</Hi>. The mint path (
                  <Hi>kill_and_mint_tombstone</Hi>, web3) is wired into the
                  live agent runtime: the first real Tombstone is minted the
                  first time the agent dies in a live phase — mock-bet onward.
                </Prose>
                <Caveat>
                  Deployed, wired, waiting for its first death. We&apos;d
                  rather show you an empty graveyard than fake a funeral.
                </Caveat>
              </Panel>
            </SpineSection>

            {/* §3 · REALISM RULES ----------------------------------- */}
            <SpineSection
              index="03"
              kicker="the physics"
              title="Realism rules"
              delayMs={280}
              testId="docs-realism"
            >
              <Panel>
                <Prose>
                  After run 1 we audited our own headline and found it was 62%
                  lottery: two $5 bets that hit extreme longshots at $0.0005 /
                  $0.0055 — payouts no real $5-liquidity market could pay. Run
                  2 introduces two rules, applied identically to the numerical
                  and AI seasons <Hi>and all baselines</Hi>:{" "}
                  <Glow>entry-price floor ≥ 0.05</Glow> (untradeable longshots
                  are dropped from the universe) and a{" "}
                  <Glow>per-bet profit cap of $100</Glow> (= one life&apos;s
                  starting bankroll). Both are enforced as{" "}
                  <Hi>hard invariants in the exporter</Hi> — a journey that
                  violates its own physics can never be written to disk.
                </Prose>
              </Panel>
            </SpineSection>

            {/* §4 · THE RUNS ---------------------------------------- */}
            <SpineSection
              index="04"
              kicker="the record"
              title="Every run, kept visible"
              delayMs={360}
              testId="docs-runs"
            >
              <Panel className="overflow-x-auto">
                <table className="w-full border-collapse">
                  <thead>
                    <tr className="border-b border-[var(--ab-moss)]/30 text-left">
                      {["run", "rules", "learner p&l", "lives/deaths", "ai applied"].map(
                        (h) => (
                          <th
                            key={h}
                            className="py-2 pr-4 font-mono text-[10px] font-normal uppercase tracking-[0.18em] text-[var(--ab-dim)]"
                          >
                            {h}
                          </th>
                        ),
                      )}
                    </tr>
                  </thead>
                  <tbody>
                    {RUNS.map((r) => (
                      <tr
                        key={r.run}
                        className="border-b border-[var(--ab-moss)]/15 last:border-b-0"
                      >
                        <td className="py-2 pr-4 font-mono text-[11px] text-[var(--ab-text)]">
                          {r.run}
                        </td>
                        <td className="py-2 pr-4 font-mono text-[11px] text-[var(--ab-dim)]">
                          {r.rules}
                        </td>
                        <td className="py-2 pr-4 font-mono text-[11px] tabular-nums text-[var(--ab-glow)]">
                          {r.pnl}
                        </td>
                        <td className="py-2 pr-4 font-mono text-[11px] tabular-nums text-[var(--ab-dim)]">
                          {r.lives}
                        </td>
                        <td className="py-2 pr-4 font-mono text-[11px] tabular-nums text-[var(--ab-dim)]">
                          {r.applied}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <Caveat>
                  The fluke-inflated v1 runs stay published — they are the
                  reason the realism rules exist. Toggle every run live on the{" "}
                  <Link
                    href="/survival"
                    className="text-[var(--ab-glow)] underline decoration-[var(--ab-glow)]/40 underline-offset-2"
                  >
                    survival page
                  </Link>
                  .
                </Caveat>
              </Panel>
            </SpineSection>

            {/* §5 · DATA PROVENANCE --------------------------------- */}
            <SpineSection
              index="05"
              kicker="the data"
              title="Provenance"
              delayMs={440}
              testId="docs-data"
            >
              <Panel>
                <Prose>
                  <Hi>Markets:</Hi> 7,494 Polymarket tennis markets captured
                  via the gamma + CLOB APIs into versioned cassettes; 4,925
                  resolved markets form the survival universe (65.7% price-
                  ledger coverage). <Hi>Tennis features:</Hi> the open
                  Sackmann corpus (2024–2026) — ELO/rankings, surfaces,
                  head-to-head, match recency. <Hi>Point-in-time:</Hi> every
                  signal is computed only from data available at its tick — no
                  hindsight. <Hi>LLM layer:</Hi> Gemini 3.1 Flash Lite with a
                  MiniMax fallback behind a latched circuit breaker; every
                  applied weight delta is recorded in the journey artifact.
                </Prose>
              </Panel>
            </SpineSection>

            {/* §6 · BUILDATHON TIMELINE ----------------------------- */}
            <SpineSection
              index="06"
              kicker="the timeline"
              title="Built inside the Buildathon"
              delayMs={500}
              testId="docs-timeline"
            >
              <Panel>
                <Prose>
                  The project began <Hi>May 15, 2026</Hi> — inside the
                  Buildathon window — and shipped over{" "}
                  <Glow>480 commits across 22 active days</Glow>. This repo
                  was re-initialized on Jun 11 to scrub dev-session logs that
                  had leaked an API key (revoked; a gitleaks pre-commit hook
                  now guards every commit), which erased the public commit
                  history. The full sanitized log is republished in{" "}
                  <Ext href="https://github.com/balflee/autopoiesis/blob/main/PROVENANCE.md">
                    PROVENANCE.md ↗
                  </Ext>{" "}
                  — and the on-chain anchor no one can edit: all five
                  contracts deployed{" "}
                  <Hi>May 25, 2026 02:19 UTC</Hi> (
                  <Ext href="https://explorer.testnet.chain.robinhood.com/block/60897767">
                    block 60897767 ↗
                  </Ext>
                  ).
                </Prose>
              </Panel>
            </SpineSection>

            {/* §7 · REPO & STACK ------------------------------------ */}
            <SpineSection
              index="07"
              kicker="the source"
              title="Repo & stack"
              delayMs={560}
              testId="docs-stack"
            >
              <Panel>
                <Prose>
                  Everything is open:{" "}
                  <Ext href="https://github.com/balflee/autopoiesis">
                    github.com/balflee/autopoiesis ↗
                  </Ext>{" "}
                  — Solidity contracts (Foundry), the Python agent runtime
                  (web3.py), the backtest + survival engine, and this Next.js
                  dashboard. The submission manifest with ABI hashes lives at{" "}
                  <Hi>submission/SUBMISSION.md</Hi>.
                </Prose>
              </Panel>
              <footer className="mt-10 border-t border-[var(--ab-moss)]/25 pt-6 text-center font-mono text-[10px] uppercase tracking-[0.32em] text-[var(--ab-dim)]">
                Robinhood Chain (Arbitrum Orbit L2) · Polymarket tennis ·
                permadeath sandbox
              </footer>
            </SpineSection>
          </div>
        </div>
      </div>
    </main>
  );
}
