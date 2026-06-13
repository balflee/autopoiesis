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

/** The genome keys the rebirth advisor moved this death (start → carry). */
function genomeMoves(inc: ReincarnationIncarnation): string[] {
  const start = inc.start_genome;
  const after = inc.carry_genome_after_advice;
  if (!start || !after) return [];
  return Object.keys(after)
    .filter((k) => Math.abs((after[k] ?? 0) - (start[k] ?? 0)) > 1e-9)
    .map((k) => `${k} ${(start[k] ?? 0).toFixed(3)}→${(after[k] ?? 0).toFixed(3)}`);
}

/** Shutdown-vs-mode-switch read from the participation split. */
function participationCall(
  thirds: readonly { third: number; placed: number; denominator: number }[],
): { label: string; shutdown: boolean } {
  const placed = thirds.map((t) => t.placed);
  const shutdown = placed[0]! > 0 && placed[2] === 0;
  return {
    shutdown,
    label: thirds
      .map((t) => `${t.placed}/${t.denominator}`)
      .join(" · "),
  };
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
        {inc.tributes_paid ? (
          <span>tributes −{money(inc.tributes_paid)}</span>
        ) : null}
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
      {inc.tributes && inc.tributes.length > 0 ? (
        <p
          data-testid={`reincarnation-tribute-${inc.incarnation}`}
          className="font-mono text-[10px] leading-relaxed text-[var(--ab-dim)]"
        >
          <span className="text-[var(--ab-text)]">at the altar ▸ </span>
          {inc.tributes
            .map(
              (t) =>
                `offered ${money(t.amount_usd)} — ${
                  t.success
                    ? "the gods granted breath"
                    : "the gods kept the money"
                }`,
            )
            .join(" · ")}
        </p>
      ) : null}
      {inc.regime_ledger ? (
        <p
          data-testid={`reincarnation-ledger-${inc.incarnation}`}
          className="font-mono text-[10px] leading-relaxed text-[var(--ab-dim)]"
        >
          <span className="text-[var(--ab-text)]">regime ledger ▸ </span>
          storm-high: {inc.regime_ledger.storm_split.high.bets} bets{" "}
          {money(inc.regime_ledger.storm_split.high.pnl)} · storm-low:{" "}
          {inc.regime_ledger.storm_split.low.bets} bets{" "}
          {money(inc.regime_ledger.storm_split.low.pnl)}
          {inc.regime_ledger.gate_counterfactuals
            .filter((c) => c.computable > 0)
            .slice(0, 1)
            .map(
              (c) =>
                ` · at γ+${c.gamma}: ${c.blocked} bets would have been blocked (${money(c.blocked_pnl)})`,
            )}
        </p>
      ) : null}
      {genomeMoves(inc).length > 0 ? (
        <p
          data-testid={`reincarnation-genome-${inc.incarnation}`}
          className="font-mono text-[10px] leading-relaxed text-[var(--ab-glow)]"
        >
          <span className="text-[var(--ab-text)]">genome moved ▸ </span>
          {genomeMoves(inc).join(" · ")}
        </p>
      ) : null}
      {inc.rebirth_note ? (
        <p className="font-mono text-[10px] leading-relaxed text-[var(--ab-dim)]">
          <span className="text-[var(--ab-glow)]">rebirth note ▸ </span>
          {inc.rebirth_note}
        </p>
      ) : null}
      {inc.prayer ? (
        <p
          data-testid={`reincarnation-prayer-${inc.incarnation}`}
          className="font-mono text-[10px] italic leading-relaxed text-[var(--ab-dim)]"
        >
          <span className="not-italic text-[var(--ab-text)]">
            dying wish ▸{" "}
          </span>
          “{inc.prayer}”
        </p>
      ) : null}
    </div>
  );
}

/** One toggle-able experiment arm beyond the numerical control. */
export interface ReincarnationArm {
  readonly key: string;
  readonly label: string;
  readonly fixture: ReincarnationFixture;
}

export interface ReincarnationShellProps {
  readonly numerical: ReincarnationFixture;
  /** The Gemini death-retrospective treatment leg, or null (legacy prop;
   * kept for backward compatibility — the A9 arms ride `arms`). */
  readonly ai?: ReincarnationFixture | null;
  /** A9 experiment arms (G0 kit-off / G1 full kit / G2 falsification),
   * each toggle-able against the numerical control. */
  readonly arms?: readonly ReincarnationArm[];
}

export function ReincarnationShell({
  numerical,
  ai = null,
  arms = [],
}: ReincarnationShellProps): JSX.Element {
  // Toggle entries: the numerical control first, then the legacy Gemini
  // leg (if present), then every A9 arm. ``mode`` is the active key.
  const entries: ReincarnationArm[] = [
    { key: "numerical", label: "numerical · control", fixture: numerical },
    ...(ai !== null
      ? [{ key: "ai", label: "ai · gemini rebirth · treatment", fixture: ai }]
      : []),
    ...arms,
  ];
  const [mode, setMode] = useState<string>("numerical");
  const fixture =
    entries.find((e) => e.key === mode)?.fixture ?? numerical;
  const hasToggle = entries.length > 1;
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
          {fixture.split.shuffled_timestamps ? (
            <span
              data-testid="reincarnation-shuffled-badge"
              className="w-fit rounded-full border border-[var(--ab-death)]/50 px-3 py-1 font-mono text-[9px] uppercase tracking-[0.22em] text-[var(--ab-death)]"
            >
              shuffled-control season — timestamps permuted, regime destroyed
              (the falsification leg)
            </span>
          ) : null}
          <h1 className="font-display text-4xl leading-tight text-[var(--ab-text)] sm:text-5xl">
            die. remember. restart at bet&nbsp;#1.
          </h1>
          <p className="max-w-2xl font-mono text-[11px] leading-relaxed text-[var(--ab-dim)]">
            Every death sends the agent back to the season&apos;s first market
            — carrying its weights, the EMA learner&apos;s aggregates
            {fixture.provider === "ai"
              ? ", and a one-paragraph rebirth retrospective"
              : ""}
            , but never the outcomes. The loop ends only when one life walks
            from
            the first bet to the last without drowning. And the money? A dead
            life&apos;s profit is <span className="text-[var(--ab-death)]">forfeit</span>.
          </p>
        </header>

        {/* ── Arm toggle (numerical control + every present arm) ────── */}
        {hasToggle ? (
          <div
            className="flex flex-wrap gap-2"
            data-testid="reincarnation-mode-toggle"
          >
            {entries.map((e) => (
              <button
                key={e.key}
                type="button"
                data-testid={`reincarnation-mode-${e.key}`}
                data-active={mode === e.key}
                onClick={() => setMode(e.key)}
                className={[
                  "rounded-full border px-4 py-1 font-mono text-[10px] uppercase tracking-[0.22em] transition-colors",
                  mode === e.key
                    ? "border-[var(--ab-glow)]/60 text-[var(--ab-glow)]"
                    : "border-[var(--ab-moss)]/30 text-[var(--ab-dim)] hover:text-[var(--ab-text)]",
                ].join(" ")}
              >
                {e.label}
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
            {fixture.tribute?.enabled ? (
              <>
                {" "}
                <span className="text-[var(--ab-glow)]">
                  And the gods take offerings:
                </span>{" "}
                a dying agent may buy a fresh lungful — minimum{" "}
                {money(fixture.tribute.min_usd)} (~
                {(100 * fixture.tribute.p_floor).toFixed(0)}% grant), rising
                to ~{(100 * fixture.tribute.p_cap).toFixed(0)}% at{" "}
                {money(fixture.tribute.full_usd)}. The offering is kept{" "}
                <span className="text-[var(--ab-text)]">win or lose</span>.
              </>
            ) : null}
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
          {fixture.gods_revenue !== undefined ? (
            <p
              data-testid="reincarnation-gods-revenue"
              className="font-mono text-[11px] leading-relaxed text-[var(--ab-dim)]"
            >
              <span className="text-[var(--ab-text)]">
                the gods&apos; revenue ▸{" "}
              </span>
              <span className="font-display text-xl text-[var(--ab-glow)] ab-glow-text">
                {money(fixture.gods_revenue)}
              </span>{" "}
              collected at the altar across {incs.length} incarnation
              {incs.length === 1 ? "" : "s"} (failed offerings included — the
              gods keep everything) ·{" "}
              <span className="text-[var(--ab-text)]">
                best single life{" "}
              </span>
              <span data-testid="reincarnation-gods-best">
                {money(
                  fixture.gods_revenue_best_incarnation ??
                    incs.reduce(
                      (acc, i) => Math.max(acc, i.tributes_paid ?? 0),
                      0,
                    ),
                )}
              </span>
              {" — "}the per-life revenue an operator would actually pocket.
            </p>
          ) : null}
          {fixture.gods_revenue !== undefined &&
          fixture.revival_earnings_total !== undefined ? (
            <p
              data-testid="reincarnation-revival-roi"
              className="font-mono text-[11px] leading-relaxed text-[var(--ab-dim)]"
            >
              <span className="text-[var(--ab-text)]">
                did buying life buy income? ▸{" "}
              </span>
              after its revivals the agent earned{" "}
              <span className="text-[var(--ab-glow)]">
                {money(fixture.revival_earnings_total)}
              </span>{" "}
              against{" "}
              <span className="text-[var(--ab-death)]">
                {money(fixture.gods_revenue)}
              </span>{" "}
              paid at the altar
              {fixture.gods_revenue > 0 ? (
                <>
                  {" "}
                  — every $1 of tribute bought{" "}
                  <span className="text-[var(--ab-text)]">
                    $
                    {(
                      fixture.revival_earnings_total / fixture.gods_revenue
                    ).toFixed(3)}
                  </span>{" "}
                  of post-revival earnings. Revival lands at peak in-flight
                  loss pressure; a fresh lungful survives ~1.4 of those
                  losses.
                </>
              ) : null}
            </p>
          ) : null}
          {fixture.falsification_metric ? (
            <p
              data-testid="reincarnation-falsification"
              className="font-mono text-[10px] leading-relaxed text-[var(--ab-dim)]"
            >
              <span className="text-[var(--ab-text)]">
                falsification metric ▸{" "}
              </span>
              terminal {fixture.falsification_metric.key} ={" "}
              <span className="text-[var(--ab-glow)]">
                {fixture.falsification_metric.value >= 0 ? "+" : ""}
                {fixture.falsification_metric.value.toFixed(3)}
              </span>{" "}
              (threshold ±{fixture.falsification_metric.threshold}) ·{" "}
              {fixture.falsification_metric.productive_calls}/
              {fixture.falsification_metric.min_productive_required} productive
              death reviews —{" "}
              {fixture.falsification_metric.evaluable ? (
                "evaluable"
              ) : (
                <span className="text-[var(--ab-death)]">
                  INCONCLUSIVE — the advisor never had enough death-boundary
                  chances to move γ; this leg neither passes nor fails
                </span>
              )}
            </p>
          ) : null}
          {(() => {
            const last = incs[incs.length - 1];
            const thirds = last?.bets_by_third;
            if (!thirds) return null;
            const call = participationCall(thirds);
            return (
              <p
                data-testid="reincarnation-participation"
                className="font-mono text-[10px] leading-relaxed text-[var(--ab-dim)]"
              >
                <span className="text-[var(--ab-text)]">
                  participation (final life, by thirds of its span) ▸{" "}
                </span>
                {call.label} bets placed/markets —{" "}
                {call.shutdown ? (
                  <span className="text-[var(--ab-death)]">
                    SHUTDOWN: it stopped betting entirely in the final third.
                    Survival by abstention is not regime intelligence; called
                    out as measured.
                  </span>
                ) : (
                  "it kept betting across the whole span (mode-switch, not " +
                  "shutdown)."
                )}
              </p>
            );
          })()}
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
              <span className="text-[var(--ab-text)]">The tribute fine print:</span>{" "}
              the gods&apos; price list is disclosed above and the cap is 0.99
              — the gods never guarantee, so a maxed offering can still fail.
              Failed offerings are kept (it is an offering, not a purchase).
              The control leg&apos;s tribute behavior is a SCRIPTED reflex
              (pay min($2,000, bankroll) when dying — disclosed as a baseline
              policy, never claimed as emergent); the treatment leg&apos;s
              deathbed choices are the LLM&apos;s own, with silence, refusal,
              and malformed answers counted separately. The frozen holdout
              and all three baselines run WITHOUT tribute, so the
              generalization check stays comparable.
            </li>
            <li>
              <span className="text-[var(--ab-text)]">Prayers are recorded, never granted:</span>{" "}
              after each death the agent may state one dying wish — what
              parameter or information it wants in its next life. The wish is
              NOT carried into the next incarnation (the experiment&apos;s
              information flow stays clean); it is logged for the designers,
              and the prayer log feeds the roadmap of what the agent gets
              next.
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
