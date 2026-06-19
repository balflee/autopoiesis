"use client";

/**
 * IncarnationLineage — Living Stage Zone Z5.
 *
 * Renders the agent's reincarnation chain: every past life folded from
 * deaths.jsonl (each a `✝` headstone with its cause + final bankroll),
 * followed by the CURRENT living incarnation (`● ALIVE`).
 *
 * Live data ONLY via useWsStore selectors:
 *   - selectReincarnationLineage → readonly IncarnationLineageEntry[]
 *   - selectIncarnationNumber    → number (the current life)
 *
 * Theme: abyss widget palette (bioluminescent lime on near-black).
 */

import { type JSX } from "react";

import { widgetPalette } from "@/lib/colorTokens";
import {
  selectIncarnationNumber,
  selectReincarnationLineage,
  useWsStore,
} from "@/lib/wsStore";

export function IncarnationLineage(): JSX.Element {
  const lineage = useWsStore(selectReincarnationLineage);
  const current = useWsStore(selectIncarnationNumber);
  const pal = widgetPalette("abyss");

  return (
    <div className="flex flex-col gap-1" data-testid="incarnation-lineage">
      <div
        className="font-mono text-[9px] uppercase tracking-[0.18em]"
        style={{ color: pal.inkMuted }}
      >
        ⟲ Lineage · reincarnations
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[9px]">
        {lineage.map((l) => (
          <span key={l.incarnation_number} style={{ color: pal.inkMuted }}>
            life {l.incarnation_number}{" "}
            <span style={{ color: pal.danger }}>✝</span>{" "}
            {l.cause.replace("_", " ")}
            <span style={{ color: pal.inkMuted }}>
              {" "}
              · ${l.final_bankroll_usd.toFixed(0)}
            </span>
          </span>
        ))}
        <span data-testid="lineage-current" style={{ color: pal.accent }}>
          life {current} ● ALIVE
        </span>
      </div>
    </div>
  );
}
