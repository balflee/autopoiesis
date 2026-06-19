"use client";

import { useWsStore, selectDecisionFeed, openBetsOf, betsOf } from "@/lib/wsStore";
import { widgetPalette } from "@/lib/colorTokens";

/**
 * Living Stage — "Positions" panel.
 *
 * The agent bets SELECTIVELY (most ticks are NO_BET), so "The Act" card — which
 * shows only the single most-recent open bet — under-represents the activity
 * and reverts to "scanning" between bets. This panel makes the betting
 * PERSISTENTLY visible: the open-position count + total at-risk exposure, the
 * recent bets (market / side / stake / edge / status), and a settled
 * win-loss + net-PnL tally. Pure-derived from the live decision feed (the same
 * 2 s poll the rest of /living reads); no props, no backend change.
 */
export function OpenPositions(): JSX.Element {
  const feed = useWsStore(selectDecisionFeed);
  const open = openBetsOf(feed); // held to resolution, newest-first
  const all = betsOf(feed); // open + settled, newest-first
  const pal = widgetPalette("abyss");

  const atRisk = open.reduce((sum, b) => sum + (b.size_usd ?? 0), 0);
  const settled = all.filter((b) => b.result === "WIN" || b.result === "LOSS");
  const wins = settled.filter((b) => b.result === "WIN").length;
  const losses = settled.filter((b) => b.result === "LOSS").length;
  const netPnl = settled.reduce((s, b) => s + (b.pnl_usd ?? 0), 0);
  const recent = all.slice(0, 6);

  return (
    <div
      className={`w-full rounded-lg border p-3 ${pal.panelFaint}`}
      data-testid="open-positions"
    >
      <div
        className="flex items-center justify-between font-mono text-[9px] uppercase tracking-[0.18em]"
        style={{ color: pal.inkMuted }}
      >
        <span>▸ Positions</span>
        <span
          data-testid="positions-summary"
          style={{ color: open.length > 0 ? pal.accent : pal.inkMuted }}
        >
          {open.length} open · ${atRisk.toFixed(2)} at risk
        </span>
      </div>

      {recent.length === 0 ? (
        <div className="mt-2 font-mono text-[11px]" style={{ color: pal.inkMuted }}>
          no bets yet — scanning for an edge it can size above the floor.
        </div>
      ) : (
        <ul className="mt-2 flex flex-col gap-1" data-testid="positions-list">
          {recent.map((b) => {
            const won = b.result === "WIN";
            const lost = b.result === "LOSS";
            const statusColor = won ? pal.accent : lost ? pal.danger : pal.ink;
            const status = won
              ? `✓ WIN +$${Math.abs(b.pnl_usd ?? 0).toFixed(2)}`
              : lost
                ? `✗ LOSS −$${Math.abs(b.pnl_usd ?? 0).toFixed(2)}`
                : "● holding";
            return (
              <li
                key={b.id}
                className="flex items-center justify-between gap-2 font-mono text-[10px]"
                style={{ color: pal.ink }}
              >
                <span className="truncate">
                  {b.market_id ?? "—"} · {b.side ?? "?"} ${(b.size_usd ?? 0).toFixed(2)}
                  {b.edge_pct != null ? ` · ${(b.edge_pct * 100).toFixed(1)}%` : ""}
                </span>
                <span className="shrink-0" style={{ color: statusColor }}>
                  {status}
                </span>
              </li>
            );
          })}
        </ul>
      )}

      {settled.length > 0 ? (
        <div
          className="mt-2 font-mono text-[9px]"
          data-testid="positions-settled"
          style={{ color: pal.inkMuted }}
        >
          settled: {wins}W–{losses}L · net{" "}
          <span style={{ color: netPnl >= 0 ? pal.accent : pal.danger }}>
            {netPnl >= 0 ? "+$" : "−$"}
            {Math.abs(netPnl).toFixed(2)}
          </span>
        </div>
      ) : null}
    </div>
  );
}
