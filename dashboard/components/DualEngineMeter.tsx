"use client";

/**
 * DualEngineMeter — two-layer visualisation of the Agent's reasoning split.
 *
 *  Layer 1 (TOP):    W_R / W_S band — proportional fill, showing how
 *                    weight is split between the Rule Engine and the
 *                    Signal Engine right now.
 *  Layer 2 (BOTTOM): α / β / ρ row — three numeric chips. α is the
 *                    Rule-engine confidence dial, β is the Signal-engine
 *                    "Twitter dominance" knob (the PRD §9 Phase 2 beat),
 *                    ρ is the cross-engine reconciliation coefficient.
 *
 * Phase 1 freezes β₁ at 0 (TP §5.3) — when that's the case the β cell
 * shows a greyed-out badge with the literal "FROZEN" label so the demo
 * audience SEES the Phase 1 → Phase 2 unlock when it happens.
 */

import type { JSX } from "react";

import { widgetPalette, type WidgetVariant } from "@/lib/colorTokens";
import { useWsStore } from "@/lib/wsStore";

const FROZEN_THRESHOLD = 1e-6;

export function DualEngineMeter({
  variant = "navy",
}: {
  /** Theme variant — `"navy"` (default, legacy) or `"abyss"` (/mock). */
  readonly variant?: WidgetVariant;
} = {}): JSX.Element {
  const weights = useWsStore((s) => s.weights);
  const pal = widgetPalette(variant);

  if (!weights) {
    return (
      <section
        data-testid="dual-engine-meter"
        data-loading="true"
        role="region"
        aria-label="Dual engine meter (waiting on backend)"
        className={`flex w-full flex-col gap-3 rounded-lg border p-4 ${pal.panel}`}
      >
        <p className="font-mono text-xs uppercase tracking-[0.2em]">
          waiting for weights frame
        </p>
        <div className={`h-4 w-full animate-pulse rounded-full ${pal.track}`} />
        <div className="grid grid-cols-3 gap-2">
          <SkeletonChip variant={variant} />
          <SkeletonChip variant={variant} />
          <SkeletonChip variant={variant} />
        </div>
      </section>
    );
  }

  // Normalise W_R/W_S so they sum to 100% even if Track B sends
  // unnormalised intermediate values.
  const total = Math.max(weights.w_r + weights.w_s, 1e-9);
  const ruleSharePct = (weights.w_r / total) * 100;
  const signalSharePct = (weights.w_s / total) * 100;

  const betaFrozen = weights.beta <= FROZEN_THRESHOLD;
  // Signal-engine dominance — the climactic "Twitter eats my brain"
  // beat happens when w_s passes ~0.7 with high β.
  const signalDominant = weights.w_s > 0.7;

  return (
    <section
      data-testid="dual-engine-meter"
      role="region"
      aria-label="Dual engine weight visualisation"
      className={`flex w-full flex-col gap-4 rounded-lg border p-4 sm:p-6 ${pal.panelSolid}`}
    >
      <header
        className={`flex items-baseline justify-between font-mono text-xs uppercase tracking-[0.2em] ${pal.textMuted}`}
      >
        <span>dual engine</span>
        <span
          data-testid="dual-engine-dominance"
          style={signalDominant ? { color: pal.accent2 } : undefined}
        >
          {signalDominant ? "signal-led" : "rule-led"}
        </span>
      </header>

      {/* Layer 1 — proportional band */}
      <div
        role="img"
        aria-label={`Rule engine ${ruleSharePct.toFixed(0)} percent, Signal engine ${signalSharePct.toFixed(0)} percent`}
        className="flex h-4 w-full overflow-hidden rounded-full"
      >
        <div
          data-testid="dual-engine-rule-share"
          className="h-full transition-[width] duration-500"
          style={{
            width: `${ruleSharePct}%`,
            backgroundColor: pal.accent,
          }}
        />
        <div
          data-testid="dual-engine-signal-share"
          className="h-full transition-[width] duration-500"
          style={{
            width: `${signalSharePct}%`,
            backgroundColor: pal.accent2,
          }}
        />
      </div>

      <div
        className={`flex justify-between font-mono text-xs uppercase tracking-[0.2em] ${pal.textMuted}`}
      >
        <span data-testid="dual-engine-w_r-label">
          W_R · {weights.w_r.toFixed(2)}
        </span>
        <span data-testid="dual-engine-w_s-label">
          W_S · {weights.w_s.toFixed(2)}
        </span>
      </div>

      {/* Layer 2 — α / β / ρ chips */}
      <div className="grid grid-cols-3 gap-2">
        <Chip
          testId="dual-engine-alpha"
          label="α"
          value={weights.alpha.toFixed(2)}
          tone={pal.accent}
          variant={variant}
        />
        <Chip
          testId="dual-engine-beta"
          label="β₁"
          value={betaFrozen ? "FROZEN" : weights.beta.toFixed(2)}
          tone={betaFrozen ? pal.inkMuted : pal.accent2}
          frozen={betaFrozen}
          variant={variant}
        />
        <Chip
          testId="dual-engine-rho"
          label="ρ"
          value={weights.rho.toFixed(2)}
          tone={weights.rho < 0 ? pal.danger : pal.ink}
          variant={variant}
        />
      </div>
    </section>
  );
}

function Chip(props: {
  testId: string;
  label: string;
  value: string;
  tone: string;
  variant: WidgetVariant;
  frozen?: boolean;
}): JSX.Element {
  const pal = widgetPalette(props.variant);
  return (
    <div
      data-testid={props.testId}
      data-frozen={props.frozen ? "true" : "false"}
      className={`flex flex-col items-center justify-center gap-1 rounded border px-3 py-2 ${
        props.variant === "abyss"
          ? "border-[var(--ab-moss)]/40"
          : "border-genesis-ink-muted/40"
      }`}
      style={props.frozen ? { opacity: 0.55 } : undefined}
    >
      <span
        className={`font-mono text-xs uppercase tracking-[0.2em] ${pal.textMuted}`}
      >
        {props.label}
      </span>
      <span
        data-testid={`${props.testId}-value`}
        className="font-mono text-base"
        style={{ color: props.tone }}
      >
        {props.value}
      </span>
    </div>
  );
}

function SkeletonChip({ variant }: { variant: WidgetVariant }): JSX.Element {
  return (
    <div
      className={`h-10 animate-pulse rounded border ${
        variant === "abyss"
          ? "border-[var(--ab-moss)]/20 bg-[var(--ab-moss)]/10"
          : "border-genesis-ink-muted/20 bg-genesis-ink-muted/10"
      }`}
    />
  );
}

export default DualEngineMeter;
