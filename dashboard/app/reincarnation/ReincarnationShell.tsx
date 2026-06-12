"use client";

/**
 * ReincarnationShell — the CLIENT body of /reincarnation (Phase 2).
 *
 * The thin async server page loads the two artifacts and hands them down; the
 * shell owns ALL markup + the numerical/AI toggle (repo convention: async
 * server pages are never rendered in vitest — the client body is the testable
 * surface). Abyss design system throughout.
 *
 * Story: the agent lives the SAME training season three times, carrying its
 * experience (weights + EMA aggregates + a sanitized rebirth note) but never
 * the market outcomes — then one learning-FROZEN cold-start pass on a
 * held-out later window is the only number allowed to claim generalization.
 */

import Link from "next/link";
import { useState, type JSX } from "react";

import type {
  ReincarnationFixture,
  ReincarnationPass,
} from "@/lib/load_reincarnation";

import PassCurves, { type PassCurveSeries } from "./PassCurves";

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

function PassCard({ p }: { p: ReincarnationPass }): JSX.Element {
  const s = p.summary;
  return (
    <div
      data-testid={`reincarnation-pass-${p.pass}`}
      className="flex flex-col gap-2 rounded-xl border border-[var(--ab-moss)]/30 bg-[var(--ab-bg-2)]/60 p-4"
    >
      <span className="flex items-baseline justify-between">
        <span className="font-display text-lg text-[var(--ab-glow)] ab-glow-text">
          pass {p.pass}
        </span>
        <span className="font-mono text-[8px] uppercase tracking-[0.2em] text-[var(--ab-dim)]">
          incarnation {p.pass}
        </span>
      </span>
      <span className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-[10px] text-[var(--ab-text)]">
        <span>pnl {money(s.pnl)}</span>
        <span>
          {s.lives} lives / {s.deaths} deaths
        </span>
        <span>{s.settled} settled</span>
        <span>{s.coverage_pct.toFixed(1)}% coverage</span>
        <span>{(100 * s.win_rate).toFixed(0)}% win</span>
      </span>
      {p.rebirth_note ? (
        <p className="font-mono text-[10px] leading-relaxed text-[var(--ab-dim)]">
          <span className="text-[var(--ab-glow)]">rebirth note ▸ </span>
          {p.rebirth_note}
        </p>
      ) : null}
    </div>
  );
}

export interface ReincarnationShellProps {
  readonly numerical: ReincarnationFixture;
  /** The Gemini rebirth-retrospective variant, or null when not generated. */
  readonly ai: ReincarnationFixture | null;
}

export function ReincarnationShell({
  numerical,
  ai,
}: ReincarnationShellProps): JSX.Element {
  const [mode, setMode] = useState<"numerical" | "ai">("numerical");
  const fixture = mode === "ai" && ai !== null ? ai : numerical;
  const h = fixture.holdout;
  const b = h.baselines;
  const opacities = [0.35, 0.6, 1.0];
  const series: PassCurveSeries[] = [
    ...fixture.passes.map((p, idx) => ({
      label: `pass-${p.pass}`,
      points: p.curve,
      opacity:
        opacities[Math.min(idx, opacities.length - 1)] ??
        1.0,
    })),
    { label: "holdout", points: h.curve, opacity: 0.9, dashed: true },
  ];
  const beatsStatic = h.summary.pnl > b.static;

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
            the same season, lived three times
          </h1>
          <p className="max-w-2xl font-mono text-[11px] leading-relaxed text-[var(--ab-dim)]">
            After each death-riddled run the agent is reborn at the season&apos;s
            first market — carrying what it learned (its fusion weights, the EMA
            learner&apos;s aggregates{ai ? ", a one-paragraph rebirth retrospective" : ""})
            but never the outcomes themselves. Then the learning is frozen and
            the agent walks into a time window it has never seen.
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
                {m === "numerical" ? "numerical" : "ai · gemini rebirth"}
              </button>
            ))}
          </div>
        ) : null}

        {/* ── §1 why ────────────────────────────────────────────────── */}
        <section className="flex flex-col gap-4">
          <SectionHead index="01" kicker="why" title="an epoch test for a living agent" />
          <p className="max-w-3xl font-mono text-[11px] leading-relaxed text-[var(--ab-dim)]">
            Phase 1 ran one chronological season: every life faced DIFFERENT
            markets, so &quot;life 5 did better than life 1&quot; mixes learning
            with luck. Phase 2 removes that confound — every pass replays the
            SAME {fixture.split.train_rows}-market training window (the first{" "}
            {Math.round(fixture.split.train_fraction * 100)}% of the timeline),
            so pass-over-pass deltas are the learning signal itself. Machine
            learning calls this an epoch; the agent calls it another life.
          </p>
        </section>

        {/* ── §2 the passes ─────────────────────────────────────────── */}
        <section className="flex flex-col gap-4">
          <SectionHead index="02" kicker="the passes" title="three incarnations" />
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            {fixture.passes.map((p) => (
              <PassCard key={p.pass} p={p} />
            ))}
          </div>
        </section>

        {/* ── §3 the overlay ────────────────────────────────────────── */}
        <section className="flex flex-col gap-4">
          <SectionHead index="03" kicker="shared axes" title="the curves, overlaid" />
          <PassCurves series={series} />
          <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-[var(--ab-dim)]">
            faint → bright: pass 1 → {fixture.passes.length} · dashed: frozen
            holdout
          </p>
        </section>

        {/* ── §4 cold-start verdict ─────────────────────────────────── */}
        <section
          data-testid="reincarnation-coldstart"
          className="flex flex-col gap-4 rounded-xl border border-[var(--ab-glow)]/30 bg-[var(--ab-bg-2)]/60 p-6"
        >
          <SectionHead
            index="04"
            kicker="the only number that counts"
            title="cold start, learning frozen"
          />
          <p className="max-w-3xl font-mono text-[11px] leading-relaxed text-[var(--ab-dim)]">
            The carried weights walk into the held-out final{" "}
            {fixture.split.holdout_rows} markets — a time window no pass ever
            saw — with learning switched OFF. No EMA updates, no retrospectives:
            whatever it earns here, it earns on structure, not memory.
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
          <p className="font-mono text-[11px] leading-relaxed text-[var(--ab-dim)]">
            {beatsStatic ? (
              <>
                The reincarnated weights{" "}
                <span className="text-[var(--ab-glow)]">out-earned</span> the
                untouched seed on unseen markets — the carried experience
                generalized.
              </>
            ) : (
              <>
                The reincarnated weights did{" "}
                <span className="text-[var(--ab-death)]">not</span> beat the
                untouched seed on unseen markets — published as measured; the
                walk-forward truth is the point of this page.
              </>
            )}
          </p>
        </section>

        {/* ── §5 honest notes ───────────────────────────────────────── */}
        <section
          data-testid="reincarnation-honest"
          className="flex flex-col gap-4"
        >
          <SectionHead index="05" kicker="fine print" title="honest notes" />
          <ol className="flex max-w-3xl list-decimal flex-col gap-3 pl-5 font-mono text-[11px] leading-relaxed text-[var(--ab-dim)]">
            <li>
              Improving on a season you have seen before includes a{" "}
              <span className="text-[var(--ab-text)]">memorization</span>{" "}
              channel. The defense is the parameter bottleneck, stated
              completely: the carried experience is the 8 fusion-weight scalars
              plus the EMA learner&apos;s ~ten derived quality aggregates
              (per-engine settlement-credit EMAs — no raw outcomes, no market
              identities){ai ? " plus one sanitized strategy-level rebirth note" : ""}.
              A ~20-scalar surface cannot store{" "}
              {fixture.split.train_rows.toLocaleString()} market outcomes, so
              cross-pass gains must come from generalizable structure. The
              artifact disclosed the carried EMA keyset per pass
              (&quot;carry.ema_keys&quot;) so the claim is auditable.
            </li>
            <li>
              The <span className="text-[var(--ab-text)]">cold-start</span>{" "}
              pass on unseen markets with learning frozen is the ONLY number
              here that can claim generalization. The training-pass trajectory
              is evidence of learning dynamics, not of edge.
            </li>
            <li>
              Rebirth retrospectives are strategy-level by{" "}
              <span className="text-[var(--ab-text)]">information flow</span>:
              the advisor&apos;s entire input is a season-aggregate window
              (weights, P&amp;L aggregates, death count) — it never receives
              market ids, player names, or outcomes — and the persisted note is
              post-sanitized.
            </li>
          </ol>
        </section>

        {/* ── §6 back-links ─────────────────────────────────────────── */}
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
