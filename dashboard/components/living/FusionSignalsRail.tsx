"use client";

/**
 * FusionSignalsRail (Living Stage Z4 — "The Mind").
 *
 * Renders the 5 signed engine signals as centered diverging bars (left = negative,
 * right = positive) from the NEWEST decision-feed entry, then the fused edge vs the
 * fee floor. Live data only — pulled from `useWsStore(selectDecisionFeed)[0]`; the
 * component never accepts live data via props. Theme via `widgetPalette("abyss")`.
 *
 * Signals come from `entry.signals` (EngineSignalMap, the 5 lowercase persisted
 * engine keys), edge from `entry.edge_pct`, fee floor from `entry.fee_floor_pct`.
 * Any missing engine renders as a zero-width bar at 0.00.
 */

import { type JSX } from "react";

import { widgetPalette } from "@/lib/colorTokens";
import { selectDecisionFeed, useWsStore } from "@/lib/wsStore";
import type { SignalEngineKey } from "@/lib/types";

const ENGINES: readonly SignalEngineKey[] = [
  "tennis_technical",
  "market_momentum",
  "surface_advantage",
  "head_to_head",
  "rest_recency",
] as const;

export function FusionSignalsRail(): JSX.Element {
  const p = useWsStore(selectDecisionFeed)[0];
  const pal = widgetPalette("abyss");
  const signals: Record<string, number> =
    (p?.signals as Record<string, number> | undefined) ?? {};

  return (
    <div className="flex flex-col gap-1.5" data-testid="fusion-rail">
      <div
        className="font-mono text-[9px] uppercase tracking-[0.18em]"
        style={{ color: pal.accent }}
      >
        ⊕ The Mind · 5 engines
      </div>
      {ENGINES.map((name) => {
        const v = signals[name] ?? 0;
        const clamped = Math.max(-1, Math.min(1, v));
        const width = Math.abs(clamped) * 50;
        const color = clamped >= 0 ? pal.accent : pal.danger;
        return (
          <div
            key={name}
            className="flex items-center gap-2 font-mono text-[8px]"
            style={{ color: pal.inkMuted }}
            data-testid={`signal-row-${name}`}
          >
            <span className="w-[88px]">{name}</span>
            <span
              className="relative h-[6px] flex-1 rounded"
              style={{ background: "#11161f" }}
            >
              <span
                className="absolute top-0 h-[6px] rounded"
                style={
                  {
                    [clamped >= 0 ? "left" : "right"]: "50%",
                    width: `${width}%`,
                    background: color,
                  } as React.CSSProperties
                }
              />
            </span>
            <span style={{ color }}>
              {clamped >= 0 ? "+" : ""}
              {clamped.toFixed(2)}
            </span>
          </div>
        );
      })}
      <div className="mt-1 font-mono text-[9px]" style={{ color: pal.accent }}>
        FUSED EDGE
      </div>
      <div className="font-mono text-sm" style={{ color: pal.ink }}>
        <span data-testid="fused-edge">
          {p?.edge_pct != null ? p.edge_pct.toFixed(3) : "—"}
        </span>
        {p?.fee_floor_pct != null && (
          <span
            className="ml-2 text-[8px]"
            data-testid="fee-floor"
            style={{ color: pal.inkMuted }}
          >
            › fee floor {p.fee_floor_pct.toFixed(3)}{" "}
            {p.edge_pct != null && p.edge_pct > p.fee_floor_pct ? "✓" : ""}
          </span>
        )}
      </div>
    </div>
  );
}
