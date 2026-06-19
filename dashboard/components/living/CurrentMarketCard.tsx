"use client";

import { useWsStore, selectDecisionFeed, openBetsOf } from "@/lib/wsStore";
import { widgetPalette } from "@/lib/colorTokens";

/**
 * Living Stage — Zone Z3 · "The Act".
 *
 * Shows the most recent OPEN bet — a BET still HELD to resolution (not yet
 * settled WIN/LOSS) — so the card stays lit on the live position instead of
 * reverting to "scanning" on the very next NO_BET tick. The agent bets
 * selectively (most ticks are NO_BET), so reading the newest decision alone
 * left this card showing "scanning" almost always even with open positions.
 * Falls back to the newest decision (→ idle "scanning" card) only when the
 * agent is genuinely FLAT (no open bets).
 *
 * Live data comes ONLY from the wsStore — no props. Theme is the abyss
 * bioluminescent widget palette.
 */
export function CurrentMarketCard(): JSX.Element {
  const openBets = openBetsOf(useWsStore(selectDecisionFeed));
  // Sticky: the latest OPEN position (held to resolution). When the agent is
  // flat (no open bets) this is undefined → the idle "scanning" card. A SETTLED
  // bet is intentionally NOT shown here (it's no longer "the act now").
  const entry = openBets[0];
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
          {openBets.length > 1 ? ` · ${openBets.length} open` : ""}
        </div>
      </div>
    </div>
  );
}
