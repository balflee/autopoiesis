/**
 * LearningDemoPanel — the Stage-1 "能学" (can-learn) controlled proof.
 *
 * Server component (no hooks): renders the validated {@link LEARNING_DEMO}
 * fixture as the single most persuasive exhibit in the project — on ONE world,
 * with ONE locked economy, the ONLY difference is whether the agent adapts its
 * fusion weights across lives. A non-learning prior dies on every seed (0%); the
 * learners survive 80–100% by discovering and up-weighting a hidden edge.
 *
 * Distinct from the real-tennis survival run above it: this is a synthetic
 * injected-edge experiment (a controlled test of the learning machinery). The
 * caveat is foregrounded, not buried.
 */
import type { JSX } from "react";

import {
  type ArmResult,
  LEARNING_ARM_KEYS,
  LEARNING_DEMO,
} from "@/lib/load_learning_demo";

const PCT = (frac: number): string => `${Math.round(frac * 100)}%`;

/** Color a survival rate: 0 reads as death, high reads as alive. */
function armTone(rate: number): { bar: string; text: string } {
  if (rate <= 0) return { bar: "bg-[var(--ab-death)]", text: "text-[var(--ab-death)]" };
  if (rate >= 1) return { bar: "bg-[var(--ab-moss)]", text: "text-[var(--ab-moss)]" };
  return { bar: "bg-[var(--ab-glow)]", text: "text-[var(--ab-glow)]" };
}

/** One arm's survival bar + roll-up stats. */
function ArmBar({ arm }: { arm: ArmResult }): JSX.Element {
  const tone = armTone(arm.survival_rate);
  const surv =
    arm.mean_surviving_incarnation === null
      ? "never survives"
      : `graduates by life ${arm.mean_surviving_incarnation.toFixed(1)}`;
  return (
    <div data-testid="learning-arm-bar">
      <div className="flex items-baseline justify-between gap-3">
        <span className="font-mono text-[11px] uppercase tracking-[0.18em] text-[var(--ab-text)]">
          {arm.label}
        </span>
        <span className={`font-display text-2xl ${tone.text}`}>
          {PCT(arm.survival_rate)}
        </span>
      </div>
      <div
        className="mt-2 h-2.5 w-full overflow-hidden rounded-full bg-[var(--ab-bg)]"
        role="img"
        aria-label={`${arm.label}: ${PCT(arm.survival_rate)} survival`}
      >
        <div
          className={`h-full rounded-full ${tone.bar}`}
          style={{ width: `${Math.max(arm.survival_rate * 100, arm.survival_rate > 0 ? 4 : 0)}%` }}
        />
      </div>
      <p className="mt-2 font-mono text-[10px] leading-relaxed tracking-wide text-[var(--ab-dim)]">
        survival {PCT(arm.survival_rate)} · best progress{" "}
        {arm.mean_best_progress_pct.toFixed(0)}% · {surv}
      </p>
    </div>
  );
}

/** Map a (incarnation, weight) series to SVG polyline points in a 300×120 box. */
function points(xs: readonly number[], ys: readonly number[], yMax: number): string {
  const xN = xs.length - 1 || 1;
  return xs
    .map((x, i) => {
      const px = 12 + (x / xs[xN]!) * 276;
      const py = 108 - (ys[i]! / yMax) * 96;
      return `${px.toFixed(1)},${py.toFixed(1)}`;
    })
    .join(" ");
}

/** The weight ratchet: the learner lifts the hidden-edge slot and cuts the
 *  over-trusted noise slot, life by life, until it crosses survival. */
function WeightRatchet(): JSX.Element {
  const wt = LEARNING_DEMO.weight_trajectory;
  const yMax = 0.8;
  const edgePts = points(wt.incarnations, wt.edge_weight, yMax);
  const noisePts = points(wt.incarnations, wt.noise_weight, yMax);
  const lastEdge = wt.edge_weight[wt.edge_weight.length - 1]!;
  return (
    <div
      data-testid="weight-ratchet"
      className="rounded-xl border border-[var(--ab-moss)]/25 bg-[var(--ab-bg-2)]/60 p-5"
    >
      <p className="font-mono text-[10px] uppercase tracking-[0.24em] text-[var(--ab-dim)]">
        why it survives · weight ratchet (EMA learner, one seed)
      </p>
      <svg
        viewBox="0 0 300 120"
        className="mt-3 h-32 w-full"
        role="img"
        aria-label="The edge slot weight climbs while the over-trusted noise slot falls across lives."
        preserveAspectRatio="none"
      >
        <polyline
          points={noisePts}
          fill="none"
          stroke="var(--ab-death)"
          strokeWidth="2"
          strokeOpacity="0.7"
        />
        <polyline points={edgePts} fill="none" stroke="var(--ab-moss)" strokeWidth="2.5" />
      </svg>
      <div className="mt-2 flex flex-wrap items-center justify-between gap-2 font-mono text-[10px] tracking-wide">
        <span className="text-[var(--ab-moss)]">
          ▲ {wt.edge_slot_label}: {wt.edge_weight[0]!.toFixed(3)} →{" "}
          {lastEdge.toFixed(3)}
        </span>
        <span className="text-[var(--ab-death)]">
          ▼ {wt.noise_slot_label}: {wt.noise_weight[0]!.toFixed(3)} →{" "}
          {wt.noise_weight[wt.noise_weight.length - 1]!.toFixed(3)}
        </span>
      </div>
      <p className="mt-3 border-l-2 border-[var(--ab-glow)]/50 pl-3 font-mono text-[10px] italic leading-relaxed text-[var(--ab-text)]">
        MiniMax, after a 6-loss streak: “{wt.minimax_quote}”
      </p>
    </div>
  );
}

/** The locked economy swept over edge strength: noise dies, edge lives, and the
 *  god's take flips positive at gain ≈ 0.2. */
function GainSweep(): JSX.Element {
  return (
    <div
      data-testid="gain-sweep"
      className="rounded-xl border border-[var(--ab-moss)]/25 bg-[var(--ab-bg-2)]/60 p-5"
    >
      <p className="font-mono text-[10px] uppercase tracking-[0.24em] text-[var(--ab-dim)]">
        honest economy · stronger edge → survives, noise dies
      </p>
      <table className="mt-3 w-full border-collapse font-mono text-[11px]">
        <thead>
          <tr className="text-[var(--ab-dim)]">
            <th className="py-1 text-left font-normal">edge (gain)</th>
            <th className="py-1 text-right font-normal">survival</th>
            <th className="py-1 text-right font-normal">god net vs seed</th>
          </tr>
        </thead>
        <tbody>
          {LEARNING_DEMO.gain_sweep.map((r) => {
            const pivot = r.net_vs_seed >= 0;
            return (
              <tr
                key={r.gain}
                className="border-t border-[var(--ab-moss)]/15 text-[var(--ab-text)]"
              >
                <td className="py-1 text-left">{r.gain.toFixed(1)}</td>
                <td className="py-1 text-right">{PCT(r.survival_rate)}</td>
                <td
                  className={`py-1 text-right ${pivot ? "text-[var(--ab-moss)]" : "text-[var(--ab-death)]"}`}
                >
                  {r.net_vs_seed >= 0 ? "+" : "−"}${Math.abs(r.net_vs_seed)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="mt-2 font-mono text-[10px] leading-relaxed text-[var(--ab-dim)]">
        locked knobs: loss ×{LEARNING_DEMO.config.economy.loss_multiplier as number} ·
        fragile {LEARNING_DEMO.config.economy.fragile_max_breath_risk_pct as number} ·
        breath {LEARNING_DEMO.config.economy.initial_breath as number}
      </p>
    </div>
  );
}

export default function LearningDemoPanel(): JSX.Element {
  const { config, caveat } = LEARNING_DEMO;
  return (
    <section
      data-testid="learning-demo-panel"
      aria-label="Can it learn — the self-evolution proof"
      className="mt-16 rounded-2xl border border-[var(--ab-glow)]/30 bg-[var(--ab-bg-2)]/40 p-6 sm:p-8"
    >
      <header className="mb-6">
        <p className="font-mono text-[10px] uppercase tracking-[0.32em] text-[var(--ab-glow)] ab-glow-text">
          stage 1 · the self-evolution proof
        </p>
        <h2 className="mt-2 font-display text-3xl text-[var(--ab-text)] sm:text-4xl">
          Can it actually <span className="italic text-[var(--ab-glow)]">learn?</span>
        </h2>
        <p className="mt-3 max-w-2xl font-mono text-[11px] leading-relaxed text-[var(--ab-dim)]">
          A separate, controlled experiment (distinct from the real-tennis season
          above). Same {config.n_rows}-market world, same locked breath economy,
          same starting prior — the edge is hidden in the slot the prior trusts
          LEAST. The only difference between arms: does the agent adapt its weights
          across lives? Seeds 0–{config.seeds.length - 1}.
        </p>
      </header>

      {/* The headline: three arms, one controlled difference. */}
      <div className="grid grid-cols-1 gap-5 sm:grid-cols-3">
        {LEARNING_ARM_KEYS.map((k) => (
          <ArmBar key={k} arm={LEARNING_DEMO.arms[k]} />
        ))}
      </div>

      <p className="mt-5 font-mono text-[11px] leading-relaxed text-[var(--ab-text)]">
        The non-learning prior dies on{" "}
        <span className="text-[var(--ab-death)]">every</span> world. Both learners
        rescue{" "}
        <span className="text-[var(--ab-moss)]">80–100%</span> — the frozen 0% arm
        is the zero-hypothesis control that makes “it learns” a falsifiable result,
        not a slogan.
      </p>

      {/* The why + the economy. */}
      <div className="mt-6 grid grid-cols-1 gap-5 lg:grid-cols-2">
        <WeightRatchet />
        <GainSweep />
      </div>

      {/* Foregrounded honesty — synthetic edge. */}
      <p
        data-testid="learning-caveat"
        className="mt-6 rounded-lg border border-[var(--ab-dim)]/40 bg-[var(--ab-bg)]/50 p-4 font-mono text-[10px] leading-relaxed text-[var(--ab-dim)]"
      >
        <span className="text-[var(--ab-text)]">honest caveat — </span>
        {caveat}
      </p>
    </section>
  );
}
