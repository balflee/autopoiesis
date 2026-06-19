"use client";
import { useWsStore, selectDivineTreasury } from "@/lib/wsStore";
import { widgetPalette } from "@/lib/colorTokens";

export function DivineTreasury(): JSX.Element {
  const total = useWsStore(selectDivineTreasury);
  const pal = widgetPalette("abyss");
  return (
    <div className={`rounded-md border p-3 text-center ${pal.panelFaint}`}>
      <div
        className="font-mono text-[9px] uppercase tracking-[0.18em]"
        style={{ color: pal.inkMuted }}
      >
        Divine Treasury ⛩
      </div>
      <div
        className="font-mono text-2xl"
        data-testid="divine-treasury-total"
        style={{ color: pal.accent2 }}
      >
        ${total.toLocaleString(undefined, { maximumFractionDigits: 0 })}
      </div>
      <div className="font-mono text-[8px]" style={{ color: pal.inkMuted }}>
        collected from this soul
      </div>
    </div>
  );
}
