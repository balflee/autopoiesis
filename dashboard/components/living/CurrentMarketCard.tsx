"use client";

import { useWsStore, selectDecisionFeed } from "@/lib/wsStore";
import { widgetPalette } from "@/lib/colorTokens";

/**
 * Living Stage — Zone Z3 · "The Act".
 *
 * Renders the NEWEST decisionFeed entry (selectDecisionFeed returns the feed
 * newest-first, so entry[0] is the latest). When that newest entry is a live
 * BET on a market it shows the Polymarket market id, the YES/NO odds, and the
 * paper bet. Otherwise (NO_BET, no entry, or a BET without a market_id) it
 * falls back to the idle "scanning" heartbeat card.
 *
 * Live data comes ONLY from the wsStore selector — no props. Theme is the
 * abyss bioluminescent widget palette.
 */
export function CurrentMarketCard(): JSX.Element {
  const entry = useWsStore(selectDecisionFeed)[0];
  const pal = widgetPalette("abyss");

  if (!entry || entry.action !== "BET" || !entry.market_id) {
    return (
      <div className={`rounded-lg border p-3 ${pal.panelFaint}`} data-testid="act-idle">
        <div
          className="font-mono text-[9px] uppercase tracking-[0.18em]"
          style={{ color: pal.inkMuted }}
        >
          ▸ The Act · now
        </div>
        <div className="font-mono text-[11px]" style={{ color: pal.inkMuted }}>
          scanning global tennis markets — no bettable match. heartbeat steady.
        </div>
      </div>
    );
  }

  const yes = entry.odds_yes;
  const no = entry.odds_no;

  return (
    <div className={`rounded-lg border p-3 ${pal.panelFaint}`}>
      <div
        className="font-mono text-[9px] uppercase tracking-[0.18em]"
        style={{ color: pal.inkMuted }}
      >
        ▸ The Act · now
      </div>
      <div className="font-mono text-sm" data-testid="act-market" style={{ color: pal.ink }}>
        {entry.market_id}
      </div>
      <div className="mt-2 flex gap-2">
        <div className="flex-1 rounded border p-1 text-center" style={{ borderColor: pal.accent }}>
          <div className="font-mono text-[8px]" style={{ color: pal.accent }}>
            YES
          </div>
          <div className="font-mono text-sm" style={{ color: pal.accent }}>
            {yes != null ? yes.toFixed(2).replace(/^0/, "") : "—"}
          </div>
        </div>
        <div className="flex-1 rounded border p-1 text-center" style={{ borderColor: pal.inkMuted }}>
          <div className="font-mono text-[8px]" style={{ color: pal.inkMuted }}>
            NO
          </div>
          <div className="font-mono text-sm" style={{ color: pal.inkMuted }}>
            {no != null ? no.toFixed(2).replace(/^0/, "") : "—"}
          </div>
        </div>
      </div>
      <div className="mt-2 rounded p-2" style={{ background: `${pal.accent}10` }} data-testid="act-bet">
        <span className="font-mono text-[11px]" style={{ color: pal.accent }}>
          ▸ BET {entry.side} ${entry.size_usd?.toFixed(0)}
          {entry.odds_yes != null && entry.side === "YES"
            ? ` @ ${entry.odds_yes.toFixed(2).replace(/^0/, "")}`
            : ""}
        </span>
        <div className="font-mono text-[8px]" style={{ color: pal.inkMuted }}>
          paper fill · holding to resolution
        </div>
      </div>
    </div>
  );
}
