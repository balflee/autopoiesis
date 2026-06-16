"use client";

/**
 * DecisionFeed — recent decisions, newest first.
 *
 * PRD §8 spec: "可展开看 LLM reasoning" — each row is a one-line
 * summary (action · side · size · result) and clicking expands an
 * inline detail panel with the LLM reasoning + the post-trade
 * reflection (if present).
 *
 * Virtualisation: capped at 50 rows by `wsStore.MAX_FEED`, but we still
 * windowed-render to keep first paint cheap and accessibility shadows
 * shallow. We render the first {@link VISIBLE_ROWS} only; the rest live
 * inside a `<details>` "show all" disclosure. This is intentionally
 * simpler than a full react-window dependency — bundle size matters
 * for the lighthouse gate.
 *
 * Loading state: when `decisionFeed.length === 0`, render an empty
 * stub with "waiting for first decision_feed frame" copy.
 */

import { useState, type JSX } from "react";

import {
  widgetPalette,
  type WidgetPalette,
  type WidgetVariant,
} from "@/lib/colorTokens";
import { selectDecisionFeed, useWsStore } from "@/lib/wsStore";
import type { DecisionFeedEntry, EngineSignalMap } from "@/lib/types";

const VISIBLE_ROWS = 8;

/**
 * v0.3.0 — the 5 persisted lowercase engine keys, in canonical render order,
 * with a short human-facing label for the per-engine signal block. Mirrors the
 * /backtest drill-down's SIGNAL_SLOT_LABEL (same repurposed payloads), kept
 * local so the live feed has no static-sweep loader dependency.
 */
const SIGNAL_ENGINE_LABELS: ReadonlyArray<
  readonly [keyof EngineSignalMap, string]
> = [
  ["tennis_technical", "ELO / Ranking"],
  ["market_momentum", "CLOB Momentum"],
  ["surface_advantage", "Surface"],
  ["head_to_head", "Head-to-Head"],
  ["rest_recency", "Rest / Recency"],
];

export function DecisionFeed({
  variant = "navy",
}: {
  /** Theme variant — `"navy"` (default, legacy) or `"abyss"` (/mock). */
  readonly variant?: WidgetVariant;
} = {}): JSX.Element {
  const feed = useWsStore(selectDecisionFeed);
  const pal = widgetPalette(variant);

  if (feed.length === 0) {
    return (
      <section
        data-testid="decision-feed"
        data-loading="true"
        role="region"
        aria-label="Recent decisions (waiting on backend)"
        className={`flex w-full flex-col gap-2 rounded-lg border p-4 ${pal.panel}`}
      >
        <p className="font-mono text-xs uppercase tracking-[0.2em]">
          waiting for decision_feed frame
        </p>
        <div className={`h-6 w-full animate-pulse rounded ${pal.track}`} />
        <div className={`h-6 w-3/4 animate-pulse rounded ${pal.track}`} />
        <div className={`h-6 w-1/2 animate-pulse rounded ${pal.track}`} />
      </section>
    );
  }

  const visible = feed.slice(0, VISIBLE_ROWS);
  const overflow = feed.slice(VISIBLE_ROWS);

  return (
    <section
      data-testid="decision-feed"
      role="region"
      aria-label="Recent decisions"
      className={`flex w-full flex-col gap-2 rounded-lg border p-4 sm:p-6 ${pal.panelSolid}`}
    >
      <header
        className={`flex items-baseline justify-between font-mono text-xs uppercase tracking-[0.2em] ${pal.textMuted}`}
      >
        <span>decision feed</span>
        <span data-testid="decision-feed-count">{feed.length} rows</span>
      </header>

      <ol className="flex flex-col" data-testid="decision-feed-list">
        {visible.map((row) => (
          <DecisionRow key={row.id} entry={row} variant={variant} />
        ))}
      </ol>

      {overflow.length > 0 && (
        <details
          className={`rounded border ${pal.borderFaint} px-2 py-1`}
          data-testid="decision-feed-overflow"
        >
          <summary
            className={`cursor-pointer font-mono text-xs uppercase tracking-[0.2em] ${pal.textMuted}`}
          >
            show all · {overflow.length} more
          </summary>
          <ol className="mt-2 flex flex-col">
            {overflow.map((row) => (
              <DecisionRow key={row.id} entry={row} variant={variant} />
            ))}
          </ol>
        </details>
      )}
    </section>
  );
}

function resultColor(
  pal: WidgetPalette,
  result?: DecisionFeedEntry["result"],
): string {
  switch (result) {
    case "WIN":
      return pal.accent;
    case "LOSS":
      return pal.danger;
    case "PENDING":
      return pal.accent2;
    default:
      return pal.inkMuted;
  }
}

/** True when the signals map carries at least one numeric per-engine score. */
function hasSignals(signals?: EngineSignalMap): boolean {
  if (!signals) return false;
  return SIGNAL_ENGINE_LABELS.some(
    ([key]) => typeof signals[key] === "number",
  );
}

function DecisionRow(props: {
  entry: DecisionFeedEntry;
  variant: WidgetVariant;
}): JSX.Element {
  const { entry, variant } = props;
  const pal = widgetPalette(variant);
  const [open, setOpen] = useState(false);
  const showSignals = hasSignals(entry.signals);
  const hasDetail = Boolean(
    entry.reasoning || entry.reflection || entry.market_id || showSignals,
  );
  const result = entry.result ?? "PENDING";
  const resultLabel = result === "PENDING" ? "·" : result;
  const sizeStr =
    typeof entry.size_usd === "number" ? `$${entry.size_usd.toFixed(0)}` : "—";
  const pnlStr =
    typeof entry.pnl_usd === "number"
      ? entry.pnl_usd >= 0
        ? `+$${entry.pnl_usd.toFixed(2)}`
        : `-$${Math.abs(entry.pnl_usd).toFixed(2)}`
      : "—";

  return (
    <li
      data-testid="decision-feed-row"
      data-result={result}
      data-action={entry.action}
      data-id={entry.id}
      data-market-id={entry.market_id ?? undefined}
      className={`border-b py-1.5 last:border-b-0 ${
        variant === "abyss"
          ? "border-[var(--ab-moss)]/15"
          : "border-genesis-ink-muted/10"
      }`}
    >
      <button
        type="button"
        onClick={() => hasDetail && setOpen((o) => !o)}
        aria-expanded={hasDetail ? open : undefined}
        aria-disabled={!hasDetail}
        data-testid="decision-feed-row-toggle"
        className={`flex w-full items-center gap-2 text-left font-mono text-xs sm:gap-3 ${
          hasDetail ? `cursor-pointer ${pal.hover}` : "cursor-default"
        }`}
      >
        <span
          aria-hidden
          className="inline-block h-2 w-2 shrink-0 rounded-full"
          style={{ backgroundColor: resultColor(pal, result) }}
        />
        <span
          className="w-14 shrink-0 uppercase tracking-[0.1em]"
          style={{
            color: entry.action === "BET" ? pal.ink : pal.inkMuted,
          }}
        >
          {entry.action}
        </span>
        <span className={`grow truncate ${pal.textMuted}`}>
          {entry.side ?? "—"}
        </span>
        <span className={`w-12 shrink-0 text-right ${pal.textStrong}`}>
          {sizeStr}
        </span>
        <span
          className="w-16 shrink-0 text-right"
          style={{ color: resultColor(pal, result) }}
        >
          {pnlStr}
        </span>
        <span
          data-testid="decision-feed-row-result"
          className="w-12 shrink-0 text-right text-[10px] uppercase tracking-[0.18em]"
          style={{ color: resultColor(pal, result) }}
        >
          {resultLabel}
        </span>
      </button>

      {/* v0.3.0 — the market this decision evaluated, surfaced inline (not
          behind the expand toggle) so the "what" reads at a glance + a
          deep-link / test can assert it without interaction. */}
      {entry.market_id && (
        <p
          data-testid="decision-feed-row-market"
          className={`mt-0.5 pl-5 font-mono text-[10px] uppercase tracking-[0.16em] ${pal.textMuted}`}
        >
          market · <span style={{ color: pal.ink }}>{entry.market_id}</span>
        </p>
      )}

      {hasDetail && open && (
        <div
          data-testid="decision-feed-row-detail"
          className={`mt-2 flex flex-col gap-2 rounded border ${pal.panelFaint} p-3`}
        >
          {/* v0.3.0 — the per-engine signal scores that drove this decision
              (the "why"). The 5 lowercase engine keys, each as a small
              diverging score chip. Renders only when at least one is present. */}
          {showSignals && entry.signals && (
            <div data-testid="decision-feed-row-signals">
              <p
                className={`font-mono text-[10px] uppercase tracking-[0.18em] ${pal.textMuted}`}
              >
                engine signals
              </p>
              <div className="mt-1 grid grid-cols-1 gap-x-4 gap-y-1.5 sm:grid-cols-2 lg:grid-cols-3">
                {SIGNAL_ENGINE_LABELS.map(([key, label]) => {
                  const score = entry.signals?.[key];
                  if (typeof score !== "number") return null;
                  return (
                    <SignalScore
                      key={key}
                      engine={key}
                      label={label}
                      value={score}
                      pal={pal}
                    />
                  );
                })}
              </div>
            </div>
          )}
          {entry.reasoning && (
            <div>
              <p
                className={`font-mono text-[10px] uppercase tracking-[0.18em] ${pal.textMuted}`}
              >
                reasoning
              </p>
              <p className={`text-sm leading-relaxed ${pal.textStrong}`}>
                {entry.reasoning}
              </p>
            </div>
          )}
          {entry.reflection && (
            <div>
              <p
                className={`font-mono text-[10px] uppercase tracking-[0.18em] ${pal.textMuted}`}
              >
                reflection
              </p>
              <p className={`text-sm leading-relaxed ${pal.textStrong}`}>
                {entry.reflection}
              </p>
            </div>
          )}
        </div>
      )}
    </li>
  );
}

/** One per-engine signal score: label + signed value + a diverging bar. */
function SignalScore(props: {
  engine: string;
  label: string;
  value: number;
  pal: WidgetPalette;
}): JSX.Element {
  const { pal } = props;
  const clamped = Math.max(-1, Math.min(1, props.value));
  const mag = Math.abs(clamped) * 50; // half-width fill %
  const positive = clamped >= 0;
  return (
    <div
      data-testid={`decision-feed-signal-${props.engine}`}
      className="flex flex-col gap-0.5"
    >
      <div className="flex items-baseline justify-between gap-2">
        <span
          className={`font-mono text-[9px] uppercase tracking-[0.12em] ${pal.textMuted}`}
        >
          {props.label}
        </span>
        <span
          className="font-mono text-[10px] tabular-nums"
          style={{ color: positive ? pal.ink : pal.danger }}
        >
          {positive ? "+" : "−"}
          {Math.abs(clamped).toFixed(2)}
        </span>
      </div>
      <div className={`relative h-1 w-full rounded-full ${pal.track}`}>
        <span
          aria-hidden
          className="absolute left-1/2 top-0 h-full w-px -translate-x-1/2"
          style={{ backgroundColor: pal.inkMuted, opacity: 0.5 }}
        />
        <span
          className="absolute top-0 h-full rounded-full"
          style={
            positive
              ? { left: "50%", width: `${mag}%`, backgroundColor: pal.accent }
              : { right: "50%", width: `${mag}%`, backgroundColor: pal.danger }
          }
        />
      </div>
    </div>
  );
}

export default DecisionFeed;
