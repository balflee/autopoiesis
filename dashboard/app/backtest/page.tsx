"use client";

/**
 * /backtest — Phase D showpiece: the INFANCY backtest, told as the abyssal
 * vital-signs read-out of the agent's seed policy (D2 / T-D-002).
 *
 * Rebuilt from the former Phase-1 "training time machine" onto the REAL
 * config-sweep result surfaced by `lib/load_static_sweep.ts` (the optimal
 * seed, the robust top-10 frontier, and a representative sample of the
 * literal bets the seed places over the precomputed real signal rows).
 *
 * The four story panels, top to bottom:
 *   1. OPTIMAL SEED   — the fusion weights as labeled bars (w_r/w_s, the
 *      α-simplex, the β-pair, ρ) + the bet-sizing knobs. Carries the
 *      slot-name-repurpose note (the engine slots hold elo / momentum /
 *      surface / h2h / rest payloads).
 *   2. METHODOLOGY    — the honest provenance: real signals, 65.7% cassette
 *      coverage, the $5 breath-bankroll cap, the ≥50-bets robustness gate.
 *   3. FRONTIER       — the sortable robust frontier (Sharpe / PnL / win /
 *      bets + config knobs), rank-1 = the optimal seed.
 *   4. BET DRILL-DOWN — a few real resolved markets with their five signal
 *      scores, the side the seed took, the outcome, and the realised P&L.
 *
 * NO learning here — that is the Page-2 (/survival) story. This page is a
 * static read of a single committed fixture; no WS, no async.
 *
 * Aesthetic: the `.abyss` design system in `app/globals.css` (--ab-* tokens
 * + keyframes), matching the roadmap landing.
 */

import { useMemo, useState, type JSX } from "react";

import {
  isWin,
  SIGNAL_SLOT_KEYS,
  SIGNAL_SLOT_LABEL,
  STATIC_SWEEP,
  type FrontierRow,
  type SampleBet,
  type SignalSlotKey,
} from "@/lib/load_static_sweep";
import { STAGE_META } from "@/lib/lifeline";
import {
  LifelineFooter,
  NextLink,
  StageShell,
} from "@/components/lifeline/StageShell";

/* ------------------------------------------------------------------ */
/* Formatting helpers                                                  */
/* ------------------------------------------------------------------ */

const pct = (frac: number): string => `${(frac * 100).toFixed(1)}%`;
const money = (n: number): string =>
  `${n < 0 ? "−" : ""}$${Math.abs(n).toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  })}`;
const money2 = (n: number): string =>
  `${n < 0 ? "−" : "+"}$${Math.abs(n).toFixed(2)}`;
const num = (n: number, dp = 3): string => n.toFixed(dp);

/* ------------------------------------------------------------------ */
/* Optimal-seed weight bars                                            */
/* ------------------------------------------------------------------ */

/** One labeled horizontal weight bar. `value` is normalised to [0,1] for the
 *  fill width; `display` is the formatted number shown at the end. */
function WeightBar({
  id,
  label,
  sub,
  value,
  display,
  accent,
}: {
  id: string;
  label: string;
  sub?: string;
  value: number;
  display: string;
  accent: boolean;
}): JSX.Element {
  const width = Math.max(2, Math.min(100, value * 100));
  return (
    <div data-testid={`weight-bar-${id}`} className="flex flex-col gap-1.5">
      <div className="flex items-baseline justify-between gap-3">
        <span className="font-mono text-[10px] uppercase tracking-[0.22em] text-[var(--ab-dim)]">
          {label}
          {sub ? (
            <span className="ml-1.5 normal-case tracking-normal text-[var(--ab-dim)]/70">
              {sub}
            </span>
          ) : null}
        </span>
        <span
          className={[
            "font-mono text-xs tabular-nums",
            accent ? "text-[var(--ab-glow)] ab-glow-text" : "text-[var(--ab-text)]",
          ].join(" ")}
        >
          {display}
        </span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--ab-bg-3)]">
        <div
          className={[
            "h-full rounded-full transition-all",
            accent
              ? "bg-[var(--ab-glow)] shadow-[0_0_10px_var(--ab-glow-soft)]"
              : "bg-[var(--ab-moss)]",
          ].join(" ")}
          style={{ width: `${width}%` }}
        />
      </div>
    </div>
  );
}

/** The single headline telemetry stat (large, glowing). */
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
      <span className={`font-display text-4xl leading-none sm:text-5xl ${cls}`}>
        {value}
      </span>
      <span className="font-mono text-[10px] uppercase tracking-[0.24em] text-[var(--ab-dim)]">
        {label}
      </span>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Frontier table (sortable)                                           */
/* ------------------------------------------------------------------ */

type SortKey = "rank" | "sharpe" | "net_pnl" | "win_rate" | "bets";

const COLUMNS: readonly {
  key: SortKey;
  label: string;
  align: "left" | "right";
}[] = [
  { key: "rank", label: "#", align: "left" },
  { key: "sharpe", label: "Sharpe", align: "right" },
  { key: "net_pnl", label: "Net P&L", align: "right" },
  { key: "win_rate", label: "Win", align: "right" },
  { key: "bets", label: "Bets", align: "right" },
];

function FrontierTable(): JSX.Element {
  const [sortKey, setSortKey] = useState<SortKey>("sharpe");
  const [asc, setAsc] = useState<boolean>(false);

  const rows = useMemo<readonly FrontierRow[]>(() => {
    const copy = [...STATIC_SWEEP.frontier];
    copy.sort((a, b) => {
      const d = a[sortKey] - b[sortKey];
      return asc ? d : -d;
    });
    return copy;
  }, [sortKey, asc]);

  const onSort = (key: SortKey): void => {
    if (key === sortKey) {
      setAsc((p) => !p);
    } else {
      setSortKey(key);
      // PnL/win/bets read better ascending-off (largest first); rank asc.
      setAsc(key === "rank");
    }
  };

  return (
    <div
      data-testid="frontier-table"
      className="overflow-x-auto rounded-xl border border-[var(--ab-moss)]/30 bg-[var(--ab-bg-2)]/60"
    >
      <table className="w-full min-w-[640px] border-collapse">
        <thead>
          <tr className="border-b border-[var(--ab-moss)]/30">
            {COLUMNS.map((col) => {
              const active = col.key === sortKey;
              return (
                <th
                  key={col.key}
                  scope="col"
                  className={[
                    "px-4 py-3 font-mono text-[10px] uppercase tracking-[0.2em]",
                    col.align === "right" ? "text-right" : "text-left",
                  ].join(" ")}
                >
                  <button
                    type="button"
                    data-testid={`frontier-sort-${col.key}`}
                    onClick={() => onSort(col.key)}
                    className={[
                      "inline-flex items-center gap-1 transition-colors hover:text-[var(--ab-glow)] focus:outline-none focus-visible:text-[var(--ab-glow)]",
                      active ? "text-[var(--ab-glow)]" : "text-[var(--ab-dim)]",
                    ].join(" ")}
                  >
                    {col.label}
                    <span aria-hidden className="text-[8px]">
                      {active ? (asc ? "▲" : "▼") : "·"}
                    </span>
                  </button>
                </th>
              );
            })}
            <th
              scope="col"
              className="px-4 py-3 text-left font-mono text-[10px] uppercase tracking-[0.2em] text-[var(--ab-dim)]"
            >
              w_r · α · ρ · risk
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const top = row.rank === 1;
            return (
              <tr
                key={row.rank}
                data-testid={`frontier-row-${row.rank}`}
                className={[
                  "border-b border-[var(--ab-moss)]/15 transition-colors hover:bg-[var(--ab-glow-soft)]",
                  top ? "bg-[var(--ab-glow-soft)]" : "",
                ].join(" ")}
              >
                <td className="px-4 py-3 font-mono text-xs tabular-nums">
                  <span
                    className={
                      top
                        ? "text-[var(--ab-glow)] ab-glow-text"
                        : "text-[var(--ab-dim)]"
                    }
                  >
                    {top ? "★" : row.rank}
                  </span>
                </td>
                <td className="px-4 py-3 text-right font-mono text-xs tabular-nums">
                  <span
                    className={
                      top
                        ? "text-[var(--ab-glow)] ab-glow-text"
                        : "text-[var(--ab-text)]"
                    }
                  >
                    {num(row.sharpe, 3)}
                  </span>
                </td>
                <td className="px-4 py-3 text-right font-mono text-xs tabular-nums text-[var(--ab-text)]">
                  {money(row.net_pnl)}
                </td>
                <td className="px-4 py-3 text-right font-mono text-xs tabular-nums text-[var(--ab-text)]">
                  {pct(row.win_rate)}
                </td>
                <td className="px-4 py-3 text-right font-mono text-xs tabular-nums text-[var(--ab-dim)]">
                  {row.bets}
                </td>
                <td className="px-4 py-3 font-mono text-[10px] tabular-nums text-[var(--ab-dim)]">
                  {num(row.w_r, 2)} · [{row.alpha.map((a) => num(a, 2)).join(" ")}] ·{" "}
                  {num(row.rho, 2)} · {pct(row.max_breath_risk_pct)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Bet drill-down                                                      */
/* ------------------------------------------------------------------ */

/** A compact signal-score chip: a label + a centred bar where the fill
 *  diverges left (negative) / right (positive) from the midline. */
function SignalChip({
  label,
  value,
}: {
  label: string;
  value: number;
}): JSX.Element {
  const clamped = Math.max(-1, Math.min(1, value));
  const mag = Math.abs(clamped) * 50; // half-width fill in %
  const positive = clamped >= 0;
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-baseline justify-between gap-2">
        <span className="font-mono text-[9px] uppercase tracking-[0.14em] text-[var(--ab-dim)]">
          {label}
        </span>
        <span
          className={[
            "font-mono text-[10px] tabular-nums",
            positive ? "text-[var(--ab-text)]" : "text-[var(--ab-death)]",
          ].join(" ")}
        >
          {clamped >= 0 ? "+" : "−"}
          {Math.abs(clamped).toFixed(2)}
        </span>
      </div>
      <div className="relative h-1 w-full rounded-full bg-[var(--ab-bg-3)]">
        {/* midline */}
        <span
          aria-hidden
          className="absolute left-1/2 top-0 h-full w-px -translate-x-1/2 bg-[var(--ab-moss)]/50"
        />
        <span
          className={[
            "absolute top-0 h-full rounded-full",
            positive ? "bg-[var(--ab-glow)]" : "bg-[var(--ab-death)]",
          ].join(" ")}
          style={
            positive
              ? { left: "50%", width: `${mag}%` }
              : { right: "50%", width: `${mag}%` }
          }
        />
      </div>
    </div>
  );
}

function BetCard({ bet }: { bet: SampleBet }): JSX.Element {
  const win = isWin(bet);
  const voided = bet.outcome === "void";
  const outcomeTone = win
    ? "text-[var(--ab-glow)] ab-glow-text"
    : voided
      ? "text-[var(--ab-dim)]"
      : "text-[var(--ab-death)]";
  return (
    <article
      data-testid={`bet-row-${bet.market_id}`}
      className="flex flex-col gap-3 rounded-xl border border-[var(--ab-moss)]/30 bg-[var(--ab-bg-2)]/60 p-4 transition-colors hover:border-[var(--ab-glow)]/40"
    >
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="flex flex-col">
          <h3 className="font-display text-xl italic text-[var(--ab-text)]">
            {bet.players[0]}{" "}
            <span className="text-[var(--ab-dim)] not-italic">vs</span>{" "}
            {bet.players[1]}
          </h3>
          <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-[var(--ab-dim)]">
            {bet.surface} · entry {bet.entry_price.toFixed(2)} · bet {money2(bet.size).replace("+", "")}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <span
            className={[
              "rounded-full border px-2.5 py-0.5 font-mono text-[10px] uppercase tracking-[0.18em]",
              bet.side === "YES"
                ? "border-[var(--ab-glow)]/50 text-[var(--ab-glow)]"
                : "border-[var(--ab-death)]/50 text-[var(--ab-death)]",
            ].join(" ")}
          >
            took {bet.side}
          </span>
          <span
            className={`font-mono text-sm tabular-nums ${outcomeTone}`}
            data-testid={`bet-pnl-${bet.market_id}`}
          >
            {voided ? "void" : money2(bet.pnl)}
          </span>
        </div>
      </header>

      <div className="grid grid-cols-1 gap-x-5 gap-y-2 sm:grid-cols-2 lg:grid-cols-5">
        {SIGNAL_SLOT_KEYS.map((key: SignalSlotKey) => (
          <SignalChip
            key={key}
            label={SIGNAL_SLOT_LABEL[key]}
            value={bet.signals[key]}
          />
        ))}
      </div>
    </article>
  );
}

/* ------------------------------------------------------------------ */
/* Methodology cards                                                   */
/* ------------------------------------------------------------------ */

function MethodCard({
  stat,
  title,
  body,
}: {
  stat: string;
  title: string;
  body: string;
}): JSX.Element {
  return (
    <div className="flex flex-col gap-2 rounded-xl border border-[var(--ab-moss)]/30 bg-[var(--ab-bg-2)]/60 p-4">
      <span className="font-display text-3xl leading-none text-[var(--ab-glow)] ab-glow-text">
        {stat}
      </span>
      <span className="font-mono text-[10px] uppercase tracking-[0.22em] text-[var(--ab-text)]">
        {title}
      </span>
      <p className="font-mono text-[11px] leading-relaxed text-[var(--ab-dim)]">
        {body}
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Section heading                                                     */
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

/* ------------------------------------------------------------------ */
/* Page                                                                */
/* ------------------------------------------------------------------ */

export default function BacktestRoute(): JSX.Element {
  const seed = STATIC_SWEEP.optimal_seed;
  const w = seed.weights;

  // The fusion weights as labeled bars. The α-simplex slots carry the
  // repurposed signal payloads; we surface that mapping inline.
  const weightBars: readonly {
    id: string;
    label: string;
    sub?: string;
    value: number;
    display: string;
    accent: boolean;
  }[] = [
    { id: "w_r", label: "W_R", sub: "reason gate", value: w.w_r, display: num(w.w_r), accent: true },
    { id: "w_s", label: "W_S", sub: "sentiment gate", value: w.w_s, display: num(w.w_s), accent: true },
    {
      id: "alpha_1",
      label: "α₁",
      sub: SIGNAL_SLOT_LABEL.tennis_technical,
      value: w.alpha[0],
      display: num(w.alpha[0]),
      accent: false,
    },
    {
      id: "alpha_2",
      label: "α₂",
      sub: SIGNAL_SLOT_LABEL.market_momentum,
      value: w.alpha[1],
      display: num(w.alpha[1]),
      accent: false,
    },
    {
      id: "alpha_3",
      label: "α₃",
      sub: SIGNAL_SLOT_LABEL.smart_money,
      value: w.alpha[2],
      display: num(w.alpha[2]),
      accent: false,
    },
    {
      id: "beta_1",
      label: "β₁",
      sub: SIGNAL_SLOT_LABEL.sentiment_llm,
      value: w.beta[0],
      display: num(w.beta[0]),
      accent: false,
    },
    {
      id: "beta_2",
      label: "β₂",
      sub: SIGNAL_SLOT_LABEL.crowd_volume,
      value: w.beta[1],
      display: num(w.beta[1]),
      accent: false,
    },
    { id: "rho", label: "ρ", sub: "fuse blend", value: w.rho, display: num(w.rho), accent: true },
  ];

  return (
    <StageShell
      meta={STAGE_META.backtest}
      heroExtra={
        /* Headline telemetry row — lives inside the hero header. */
        <div
          className="ab-reveal mt-10 grid grid-cols-2 gap-6 sm:grid-cols-4"
          style={{ animationDelay: "360ms" }}
        >
          <HeadlineStat value={num(seed.sharpe, 3)} label="Sharpe" />
          <HeadlineStat value={pct(seed.win_rate)} label="win rate" />
          <HeadlineStat value={money(seed.net_pnl)} label="net P&L" tone="text" />
          <HeadlineStat value={`${seed.bets}`} label="qualified bets" tone="text" />
        </div>
      }
      footer={
        <LifelineFooter
          note={
            <>
              {STATIC_SWEEP.task_id} · {STATIC_SWEEP.sprint} · static sweep v
              {STATIC_SWEEP.schema_version}
            </>
          }
          nav={
            <NextLink
              href={STAGE_META.survival.href}
              ariaLabel="Next stage: learning to survive"
            >
              next · learning to survive ▸
            </NextLink>
          }
        />
      }
    >
      {/* ---- 01 · OPTIMAL SEED ---------------------------------- */}
        <section
          data-testid="optimal-seed-panel"
          className="ab-reveal mb-16"
          style={{ animationDelay: "120ms" }}
        >
          <SectionHead index="01" kicker="optimal seed" title="The fusion weights" />

          <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
            <div className="flex flex-col gap-3.5 rounded-xl border border-[var(--ab-moss)]/30 bg-[var(--ab-bg-2)]/60 p-5">
              {weightBars.map((b) => (
                <WeightBar key={b.id} {...b} />
              ))}
            </div>

            <div className="flex flex-col gap-4">
              {/* Sizing knobs. */}
              <div className="grid grid-cols-3 gap-3">
                <div className="rounded-lg border border-[var(--ab-moss)]/30 bg-[var(--ab-bg-2)]/60 p-3">
                  <div className="font-mono text-base tabular-nums text-[var(--ab-text)]">
                    {pct(seed.sizing.max_breath_risk_pct)}
                  </div>
                  <div className="mt-1 font-mono text-[9px] uppercase leading-tight tracking-[0.16em] text-[var(--ab-dim)]">
                    max breath risk
                  </div>
                </div>
                <div className="rounded-lg border border-[var(--ab-moss)]/30 bg-[var(--ab-bg-2)]/60 p-3">
                  <div className="font-mono text-base tabular-nums text-[var(--ab-text)]">
                    {num(seed.sizing.min_confidence, 3)}
                  </div>
                  <div className="mt-1 font-mono text-[9px] uppercase leading-tight tracking-[0.16em] text-[var(--ab-dim)]">
                    min confidence
                  </div>
                </div>
                <div className="rounded-lg border border-[var(--ab-moss)]/30 bg-[var(--ab-bg-2)]/60 p-3">
                  <div className="font-mono text-base tabular-nums text-[var(--ab-text)]">
                    ${seed.sizing.min_bet_size_usd.toFixed(0)}
                  </div>
                  <div className="mt-1 font-mono text-[9px] uppercase leading-tight tracking-[0.16em] text-[var(--ab-dim)]">
                    min bet size
                  </div>
                </div>
              </div>

              {/* Slot-name repurpose note. */}
              <div
                data-testid="slot-repurpose-note"
                className="rounded-xl border border-[var(--ab-glow)]/25 bg-[var(--ab-bg-3)]/50 p-4"
              >
                <p className="font-mono text-[10px] uppercase tracking-[0.22em] text-[var(--ab-glow)]/90">
                  slot-name repurpose
                </p>
                <p className="mt-2 font-mono text-[11px] leading-relaxed text-[var(--ab-dim)]">
                  The five engine slots keep their legacy keys but carry tennis
                  payloads: <span className="text-[var(--ab-text)]">elo</span>,{" "}
                  <span className="text-[var(--ab-text)]">CLOB momentum</span>,{" "}
                  <span className="text-[var(--ab-text)]">surface</span>,{" "}
                  <span className="text-[var(--ab-text)]">head-to-head</span>, and{" "}
                  <span className="text-[var(--ab-text)]">rest / recency</span>. The
                  α-simplex weights the first three; the β-pair weights the last two.
                </p>
              </div>
            </div>
          </div>
        </section>

        {/* ---- 02 · METHODOLOGY ----------------------------------- */}
        <section
          data-testid="methodology-panel"
          className="ab-reveal mb-16"
          style={{ animationDelay: "200ms" }}
        >
          <SectionHead index="02" kicker="how it was earned" title="Honest provenance" />

          <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <MethodCard
              stat="real"
              title="real signals"
              body="Every score is a genuine pre-match read — elo, CLOB price momentum, surface, head-to-head, rest — computed at entry time from cached Polymarket + ranking data. No look-ahead."
            />
            <MethodCard
              stat="65.7%"
              title="cassette coverage"
              body="Of the captured tennis market universe, 65.7% resolved to a settled outcome we can score against. The sweep is graded only on those."
            />
            <MethodCard
              stat="$5"
              title="breath-bankroll cap"
              body="Each bet is sized against a tiny $5 breath-bankroll cap — survival pressure from day one. No martingale, no all-in; the seed must edge its way up."
            />
            <MethodCard
              stat="≥50"
              title="min-bets gate"
              body="A config only joins the robust frontier if it places at least 50 qualified bets — Sharpe on a handful of lucky tickets does not count."
            />
          </div>
        </section>

        {/* ---- 03 · FRONTIER -------------------------------------- */}
        <section className="ab-reveal mb-16" style={{ animationDelay: "280ms" }}>
          <SectionHead index="03" kicker="robust frontier" title="Top-10 by Sharpe" />
          <p className="mt-3 max-w-2xl font-mono text-[11px] leading-relaxed text-[var(--ab-dim)]">
            The survivors of the sweep — every config clearing the ≥50-bet gate,
            ranked. Click a column to re-sort.{" "}
            <span className="text-[var(--ab-glow)]">★</span> marks the seed the
            agent is born with.
          </p>
          <div className="mt-6">
            <FrontierTable />
          </div>
        </section>

        {/* ---- 04 · BET DRILL-DOWN -------------------------------- */}
        <section
          data-testid="bet-drilldown"
          className="ab-reveal"
          style={{ animationDelay: "360ms" }}
        >
          <SectionHead index="04" kicker="bet drill-down" title="Real resolved markets" />
          <p className="mt-3 max-w-2xl font-mono text-[11px] leading-relaxed text-[var(--ab-dim)]">
            A representative sample of the literal bets the seed placed over the
            cached signals — the five signal scores it read, the side it took, the
            real outcome, and the realised P&L.
          </p>
          <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-2">
            {STATIC_SWEEP.sample_bets.map((bet) => (
              <BetCard key={bet.market_id} bet={bet} />
            ))}
          </div>
        </section>
    </StageShell>
  );
}
