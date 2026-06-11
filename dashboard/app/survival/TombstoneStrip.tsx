"use client";

/**
 * TombstoneStrip — the graveyard of dead lives (E2).
 *
 * Six lives die before the seventh survives; each leaves a Tombstone. This
 * strip lays them out as small grave-markers in the permadeath color, echoing
 * the TombstoneMintAnimation feel (token id + cause + a quiet "last words"
 * epitaph). As the playback scrubber crosses a death's step the matching
 * tombstone lights up — the audience watches each life flicker out in time.
 *
 * Reads the adapter's {@link SurvivalTombstone} list — no WS store. Abyssal.
 */

import type { JSX } from "react";

import type { SurvivalTombstone } from "@/lib/load_survival_journey";

/** A short epitaph per cause — the agent's "last words" beat, telegraphed. */
function epitaph(cause: string): string {
  switch (cause) {
    case "breath_depleted":
      return "ran out of breath";
    default:
      return cause.replace(/_/g, " ");
  }
}

const money = (n: number): string =>
  `${n < 0 ? "−" : "+"}$${Math.abs(n).toLocaleString(undefined, {
    maximumFractionDigits: 0,
  })}`;

/** A minimal tombstone glyph (rounded-top slab). */
function Headstone({ active }: { active: boolean }): JSX.Element {
  return (
    <svg
      width="22"
      height="26"
      viewBox="0 0 22 26"
      fill="none"
      aria-hidden
      className="flex-none"
    >
      <path
        d="M3 25V11a8 8 0 0 1 16 0v14H3Z"
        fill={active ? "rgba(255,107,74,0.18)" : "rgba(255,107,74,0.06)"}
        stroke="var(--ab-death)"
        strokeWidth={active ? 1.6 : 1}
        strokeOpacity={active ? 1 : 0.55}
      />
      <path
        d="M11 8v8M7 12h8"
        stroke="var(--ab-death)"
        strokeWidth={1}
        strokeOpacity={active ? 0.9 : 0.45}
      />
    </svg>
  );
}

export interface TombstoneStripProps {
  readonly tombstones: readonly SurvivalTombstone[];
  /** Current global step index — used to flag the active / passed tombstones. */
  readonly activeIndex: number;
}

export function TombstoneStrip({
  tombstones,
  activeIndex,
}: TombstoneStripProps): JSX.Element {
  if (tombstones.length === 0) {
    return (
      <section
        data-testid="tombstone-strip"
        data-empty="true"
        className="rounded-xl border border-[var(--ab-moss)]/30 bg-[var(--ab-bg-2)]/60 p-4"
      >
        <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-[var(--ab-dim)]">
          no deaths — the agent never fell
        </p>
      </section>
    );
  }

  return (
    <section
      data-testid="tombstone-strip"
      role="region"
      aria-label="The graveyard — lives that died"
      className="flex w-full flex-col gap-3 rounded-xl border border-[var(--ab-moss)]/30 bg-[var(--ab-bg-2)]/60 p-4 text-[var(--ab-text)] sm:p-5"
    >
      <header className="flex items-baseline justify-between font-mono text-[10px] uppercase tracking-[0.28em] text-[var(--ab-dim)]">
        <span>graveyard</span>
        <span style={{ color: "var(--ab-death)" }}>
          {tombstones.length} death{tombstones.length === 1 ? "" : "s"}
        </span>
      </header>

      <ol className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-6">
        {tombstones.map((t) => {
          // "Reached" once the scrubber is at-or-past this death's step.
          const reached = activeIndex >= t.stepIndex;
          // "Active" when the scrubber is the closest it gets — the death beat.
          const active =
            activeIndex >= t.stepIndex && activeIndex <= t.stepIndex + 4;
          return (
            <li
              key={t.lifeIdx}
              data-testid={`tombstone-${t.lifeIdx}`}
              data-reached={reached ? "true" : "false"}
              data-active={active ? "true" : "false"}
              className={[
                "flex flex-col gap-1.5 rounded-lg border p-3 transition-all duration-300",
                active
                  ? "border-[var(--ab-death)] bg-[var(--ab-death)]/10"
                  : reached
                    ? "border-[var(--ab-death)]/40 bg-[var(--ab-bg-3)]/50"
                    : "border-[var(--ab-moss)]/25 bg-[var(--ab-bg-3)]/30 opacity-55",
              ].join(" ")}
            >
              <div className="flex items-center gap-2">
                <Headstone active={active} />
                <div className="flex flex-col">
                  <span className="font-mono text-[9px] uppercase tracking-[0.2em] text-[var(--ab-dim)]">
                    life
                  </span>
                  <span
                    data-testid={`tombstone-life-${t.lifeIdx}`}
                    className="font-display text-xl leading-none text-[var(--ab-text)]"
                  >
                    {t.lifeIdx}
                  </span>
                </div>
              </div>
              <p
                className="font-mono text-[9px] italic lowercase tracking-[0.04em]"
                style={{ color: "var(--ab-death)" }}
              >
                {epitaph(t.cause)}
              </p>
              <dl className="flex items-baseline justify-between font-mono text-[9px] uppercase tracking-[0.14em] text-[var(--ab-dim)]">
                <span>{t.bets} bets</span>
                <span
                  className="normal-case"
                  style={{ color: t.pnl >= 0 ? "var(--ab-glow)" : "var(--ab-death)" }}
                >
                  {money(t.pnl)}
                </span>
              </dl>
              <span className="font-mono text-[8px] uppercase tracking-[0.18em] text-[var(--ab-dim)]/60">
                tomb #{t.tombstoneTokenId || "—"}
              </span>
            </li>
          );
        })}
      </ol>

      <p className="font-mono text-[10px] italic leading-relaxed tracking-wide text-[var(--ab-dim)]">
        each marker is an on-chain Tombstone NFT — a life that ran out of breath
        before it learned to trust the right engines.
      </p>
    </section>
  );
}

export default TombstoneStrip;
