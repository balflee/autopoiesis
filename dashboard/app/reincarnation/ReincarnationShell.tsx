"use client";

/**
 * ReincarnationShell — the CLIENT body of /reincarnation (Phase 2,
 * GROUNDHOG design v2).
 *
 * Story: die → remember → restart at bet #1 → loop until one life survives
 * to the season's final bet. The permadeath-economics rule: a dead
 * incarnation's profit is FORFEIT (scored zero) — the headline belongs to
 * the surviving life only. The thin async server page loads the artifacts;
 * this shell owns ALL markup + the numerical/AI toggle (repo convention:
 * async pages are never rendered in vitest).
 */

import Link from "next/link";
import { useState, type JSX } from "react";

import type {
  ReincarnationFixture,
  ReincarnationIncarnation,
} from "@/lib/load_reincarnation";

import PassCurves, { type PassCurveSeries } from "./PassCurves";
import SurvivalFrontier from "./SurvivalFrontier";

const money = (n: number): string =>
  `${n < 0 ? "−" : ""}$${Math.abs(n).toLocaleString(undefined, {
    maximumFractionDigits: 0,
  })}`;

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
      <h2 className="font-display text-2xl text-[var(--ab-text)] sm:text-3xl">
        {title}
      </h2>
    </div>
  );
}

/** Incarnation table rows: first 8 + (gap marker) + final/survivor. */
function tableRows(
  incs: readonly ReincarnationIncarnation[],
): (ReincarnationIncarnation | "gap")[] {
  if (incs.length <= 9) return [...incs];
  return [...incs.slice(0, 8), "gap", incs[incs.length - 1]!];
}

function IncarnationRow({ inc }: { inc: ReincarnationIncarnation }): JSX.Element {
  return (
    <div
      data-testid={`reincarnation-inc-${inc.incarnation}`}
      className={[
        "flex flex-col gap-1 rounded-lg border p-3",
        inc.died
          ? "border-[var(--ab-moss)]/25 bg-[var(--ab-bg-2)]/50"
          : "border-[var(--ab-glow)]/60 bg-[var(--ab-glow-soft)]",
      ].join(" ")}
    >
      <span className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span
          className={[
            "font-display text-base leading-none",
            inc.died
              ? "text-[var(--ab-text)]"
              : "text-[var(--ab-glow)] ab-glow-text",
          ].join(" ")}
        >
          incarnation {inc.incarnation}
        </span>
        <span className="font-mono text-[9px] uppercase tracking-[0.18em] text-[var(--ab-dim)]">
          {inc.died
            ? `died · ${inc.settled} settled · ${inc.progress_pct.toFixed(1)}% of the season`
            : `SURVIVED THE SEASON · ${inc.settled} settled`}
        </span>
      </span>
      <span className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-[10px] text-[var(--ab-text)]">
        <span>
          held{" "}
          <span className={inc.died ? "line-through opacity-60" : ""}>
            {money(inc.pnl_at_death)}
          </span>
        </span>
        <span>
          scored{" "}
          <span className={inc.died ? "text-[var(--ab-death)]" : "text-[var(--ab-glow)]"}>
            {money(inc.scored_pnl)}
          </span>
        </span>
        <span>{(100 * inc.win_rate).toFixed(0)}% win</span>
        {inc.advisor.called ? (
          <span>
            advisor: {inc.advisor.proposals} proposals / {inc.advisor.applied}{" "}
            applied
          </span>
        ) : null}
      </span>
      {inc.rebirth_note ? (
        <p className="font-mono text-[10px] leading-relaxed text-[var(--ab-dim)]">
          <span className="text-[var(--ab-glow)]">rebirth note ▸ </span>
          {inc.rebirth_note}
        </p>
      ) : null}
    </div>
  );
}

export interface ReincarnationShellProps {
  readonly numerical: ReincarnationFixture;
  /** The Gemini death-retrospective treatment leg, or null. */
  readonly ai: ReincarnationFixture | null;
}

export function ReincarnationShell({
  numerical,
  ai,
}: ReincarnationShellProps): JSX.Element {
  const [mode, setMode] = useState<"numerical" | "ai">("numerical");
  const fixture = mode === "ai" && ai !== null ? ai : numerical;
  const incs = fixture.incarnations;
  const h = fixture.holdout;
  const b = h.baselines;
  const curveSeries: PassCurveSeries[] = [
    ...incs
      .filter((inc) => inc.curve !== undefined && inc.curve.length > 0)
      .map((inc, idx, kept) => ({
        label: `inc-${inc.incarnation}`,
        points: inc.curve ?? [],
        opacity: 0.3 + 0.7 * ((idx + 1) / Math.max(1, kept.length)),
      })),
    { label: "holdout", points: h.curve, opacity: 0.9, dashed: true },
  ];
  const best = incs.reduce(
    (acc, inc) => Math.max(acc, inc.progress_pct),
    0,
  );

  return (
    <main
      data-testid="reincarnation-route"
      className="abyss min-h-screen px-6 py-16 sm:px-10"
    >
      <div className="mx-auto flex max-w-4xl flex-col gap-14">
        {/* ── Hero ──────────────────────────────────────────────────── */}
        <header className="flex flex-col gap-3">
          <span className="font-mono text-[10px] uppercase tracking-[0.32em] text-[var(--ab-glow)]">
            phase 2 · the reincarnation experiment
          </span>
          <h1 className="font-display text-4xl leading-tight text-[var(--ab-text)] sm:text-5xl">
            die. remember. restart at bet&nbsp;#1.
          </h1>
          <p className="max-w-2xl font-mono text-[11px] leading-relaxed text-[var(--ab-dim)]">
            Every death sends the agent back to the season&apos;s first market
            — carrying its weights, the EMA learner&apos;s aggregates
            {ai ? ", and a one-paragraph rebirth retrospective" : ""}, but
            never the outcomes. The loop ends only when one life walks from
            the first bet to the last without drowning. And the money? A dead
            life&apos;s profit is <span className="text-[var(--ab-death)]">forfeit</span>.
          </p>
        </header>

        {/* ── Provider toggle (only when the AI variant exists) ─────── */}
        {ai !== null ? (
          <div className="flex gap-2" data-testid="reincarnation-mode-toggle">
            {(["numerical", "ai"] as const).map((m) => (
              <button
                key={m}
                type="button"
                data-testid={`reincarnation-mode-${m}`}
                data-active={mode === m}
                onClick={() => setMode(m)}
                className={[
                  "rounded-full border px-4 py-1 font-mono text-[10px] uppercase tracking-[0.22em] transition-colors",
                  mode === m
                    ? "border-[var(--ab-glow)]/60 text-[var(--ab-glow)]"
                    : "border-[var(--ab-moss)]/30 text-[var(--ab-dim)] hover:text-[var(--ab-text)]",
                ].join(" ")}
              >
                {m === "numerical"
                  ? "numerical · control"
                  : "ai · gemini rebirth · treatment"}
              </button>
            ))}
          </div>
        ) : null}

        {/* ── §1 the rule ───────────────────────────────────────────── */}
        <section className="flex flex-col gap-4">
          <SectionHead
            index="01"
            kicker="the rule"
            title="dead men collect nothing"
          />
          <p className="max-w-3xl font-mono text-[11px] leading-relaxed text-[var(--ab-dim)]">
            One incarnation = one life, starting at market #1 of the{" "}
            {fixture.split.train_rows.toLocaleString()}-market training
            window. Die anywhere along the way and the P&amp;L you were
            holding is <span className="text-[var(--ab-death)]">zeroed</span>{" "}
            — experience reincarnates, money does not. Only a life that
            reaches the final bet keeps its earnings, and that number alone is
            the headline. (A life that survives by never betting keeps $0 —
            immortality through abstention pays the same as death. The rule
            is stated as measured.)
          </p>
        </section>

        {/* ── §2 the frontier ───────────────────────────────────────── */}
        <section className="flex flex-col gap-4">
          <SectionHead
            index="02"
            kicker="the survival frontier"
            title="how far each life got"
          />
          <SurvivalFrontier incarnations={incs} />
          <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-[var(--ab-dim)]">
            {incs.length} incarnation{incs.length === 1 ? "" : "s"} · best
            depth {best.toFixed(1)}% · dashed line = the finish line
          </p>
        </section>

        {/* ── §3 the lives ──────────────────────────────────────────── */}
        <section className="flex flex-col gap-3">
          <SectionHead index="03" kicker="the lives" title="incarnation log" />
          {tableRows(incs).map((row, i) =>
            row === "gap" ? (
              <p
                key={`gap-${i}`}
                className="px-3 font-mono text-[10px] uppercase tracking-[0.2em] text-[var(--ab-dim)]/70"
              >
                … {incs.length - 9} more incarnations (scalars in the artifact)
                …
              </p>
            ) : (
              <IncarnationRow key={row.incarnation} inc={row} />
            ),
          )}
        </section>

        {/* ── §4 the verdict ────────────────────────────────────────── */}
        <section
          data-testid="reincarnation-verdict"
          className="flex flex-col gap-4 rounded-xl border border-[var(--ab-glow)]/30 bg-[var(--ab-bg-2)]/60 p-6"
        >
          <SectionHead index="04" kicker="the verdict" title="did it learn to live?" />
          {fixture.survived ? (
            <p className="max-w-3xl font-mono text-[11px] leading-relaxed text-[var(--ab-dim)]">
              Incarnation{" "}
              <span className="text-[var(--ab-glow)] ab-glow-text">
                {fixture.surviving_incarnation}
              </span>{" "}
              survived the whole season and kept{" "}
              <span className="font-display text-2xl text-[var(--ab-glow)] ab-glow-text">
                {money(fixture.headline_pnl)}
              </span>{" "}
              — the only money this experiment recognizes.
            </p>
          ) : (
            <p className="max-w-3xl font-mono text-[11px] leading-relaxed text-[var(--ab-dim)]">
              No life survived within the {incs.length}-incarnation cap — best
              depth{" "}
              <span className="text-[var(--ab-text)]">{best.toFixed(1)}%</span>{" "}
              of the season. Headline:{" "}
              <span className="font-display text-2xl text-[var(--ab-death)]">
                $0
              </span>
              . Published as measured — under the death-blind learning rules
              this is the PREDICTED outcome, and that prediction is the
              experiment.
            </p>
          )}
          {fixture.provider === "ai" ? (
            <p className="font-mono text-[10px] leading-relaxed text-[var(--ab-dim)]">
              rebirth telemetry: {fixture.rebirth.calls}/
              {fixture.rebirth.expected} death reviews ·{" "}
              {fixture.rebirth.productive} productive ·{" "}
              {fixture.rebirth.applied} weight deltas applied
            </p>
          ) : null}
        </section>

        {/* ── §5 incarnation curves (the kept ones) ─────────────────── */}
        {curveSeries.length > 1 ? (
          <section className="flex flex-col gap-4">
            <SectionHead
              index="05"
              kicker="shared axes"
              title="the kept curves"
            />
            <PassCurves series={curveSeries} />
            <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-[var(--ab-dim)]">
              faint → bright: earlier → later incarnations · dashed: frozen
              holdout
            </p>
          </section>
        ) : null}

        {/* ── §6 cold start ─────────────────────────────────────────── */}
        <section
          data-testid="reincarnation-coldstart"
          className="flex flex-col gap-4 rounded-xl border border-[var(--ab-moss)]/30 bg-[var(--ab-bg-2)]/60 p-6"
        >
          <SectionHead
            index="06"
            kicker="generalization check"
            title="cold start, learning frozen"
          />
          <p className="max-w-3xl font-mono text-[11px] leading-relaxed text-[var(--ab-dim)]">
            Whatever weights the loop ended with walk into the held-out final{" "}
            {fixture.split.holdout_rows.toLocaleString()} markets — a time
            window no incarnation ever saw — with learning switched OFF.
          </p>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <div className="flex flex-col gap-1">
              <span className="font-display text-3xl text-[var(--ab-glow)] ab-glow-text">
                {money(h.summary.pnl)}
              </span>
              <span className="font-mono text-[9px] uppercase tracking-[0.2em] text-[var(--ab-dim)]">
                frozen agent
              </span>
            </div>
            <div className="flex flex-col gap-1">
              <span className="font-display text-3xl text-[var(--ab-text)]">
                {money(b.static)}
              </span>
              <span className="font-mono text-[9px] uppercase tracking-[0.2em] text-[var(--ab-dim)]">
                static seed
              </span>
            </div>
            <div className="flex flex-col gap-1">
              <span className="font-display text-3xl text-[var(--ab-text)]">
                {money(b.random)}
              </span>
              <span className="font-mono text-[9px] uppercase tracking-[0.2em] text-[var(--ab-dim)]">
                random
              </span>
            </div>
            <div className="flex flex-col gap-1">
              <span className="font-display text-3xl text-[var(--ab-death)]">
                {money(b.always_favorite)}
              </span>
              <span className="font-mono text-[9px] uppercase tracking-[0.2em] text-[var(--ab-dim)]">
                always favorite
              </span>
            </div>
          </div>
        </section>

        {/* ── §7 honest notes ───────────────────────────────────────── */}
        <section
          data-testid="reincarnation-honest"
          className="flex flex-col gap-4"
        >
          <SectionHead index="07" kicker="fine print" title="honest notes" />
          <ol className="flex max-w-3xl list-decimal flex-col gap-3 pl-5 font-mono text-[11px] leading-relaxed text-[var(--ab-dim)]">
            <li>
              <span className="text-[var(--ab-text)]">Permadeath economics:</span>{" "}
              dead incarnations score zero — you carry experience across
              deaths, never money. The headline belongs to the surviving life
              only, and an all-abstention immortal would also score $0.
            </li>
            <li>
              <span className="text-[var(--ab-text)]">The carried surface</span>{" "}
              is 8 fusion-weight scalars + the EMA learner&apos;s ~8 derived
              quality aggregates{" "}
              {fixture.provider === "ai"
                ? "+ one sanitized strategy-level rebirth note "
                : ""}
              — ~20 scalars cannot store{" "}
              {fixture.split.train_rows.toLocaleString()} outcomes (the
              memorization defense), and the artifact discloses the carried
              EMA keyset per incarnation.
            </li>
            <li>
              <span className="text-[var(--ab-text)]">Control vs treatment:</span>{" "}
              the numerical leg&apos;s gradient is death-blind — per-bet PnL
              credit, no breath or death term anywhere — so it is PREDICTED to
              plateau at a statistically similar death depth forever. The AI
              leg&apos;s advisor sees the death context (position in the
              season, stake and win-rate statistics, breath physics, plus an
              anonymous per-bet pnl tail that carries no market identities and
              cannot be mapped back to specific markets) with only the
              existing six weight keys as levers — if
              &quot;bet smaller to live longer&quot; appears, it EMERGED, it
              was not scripted.
            </li>
            <li>
              <span className="text-[var(--ab-text)]">The physics is stacked against immortality:</span>{" "}
              at $5 stakes a loss hits breath at 5× while a win returns ~$4 —
              breath expectation ≈ −1.2 per settled bet. Surviving ~1,000 bets
              by luck alone is a ~0.2% lottery; that asymmetry is the point of
              handing the agent its death context.
            </li>
            <li>
              The <span className="text-[var(--ab-text)]">cold-start</span>{" "}
              holdout (unseen time window, learning frozen) is the only number
              that can claim generalization.
            </li>
            <li>
              <span className="text-[var(--ab-text)]">Design history:</span>{" "}
              v1 of this experiment ran three fixed &quot;passes&quot; where a
              death continued mid-season — one pass quietly contained seven
              lives. The user&apos;s correction defines v2: death must send
              you back to bet #1, and dead lives keep nothing. v1&apos;s
              numbers remain in the README for the record.
            </li>
          </ol>
        </section>

        {/* ── §8 back-links ─────────────────────────────────────────── */}
        <nav className="flex flex-col gap-2 border-t border-[var(--ab-moss)]/30 pt-6">
          <Link
            href="/survival"
            className="inline-flex w-fit items-center gap-2 font-mono text-[10px] uppercase tracking-[0.28em] text-[var(--ab-dim)] transition-colors hover:text-[var(--ab-glow)]"
          >
            ◂ phase 1 · backtest with ai — /survival
          </Link>
          <Link
            href="/docs"
            className="inline-flex w-fit items-center gap-2 font-mono text-[10px] uppercase tracking-[0.28em] text-[var(--ab-dim)] transition-colors hover:text-[var(--ab-glow)]"
          >
            verify everything ▸ /docs
          </Link>
        </nav>
      </div>
    </main>
  );
}

export default ReincarnationShell;
