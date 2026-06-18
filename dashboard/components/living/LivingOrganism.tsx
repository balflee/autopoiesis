"use client";
import { useWsStore, selectVitals, selectIncarnationNumber } from "@/lib/wsStore";
import { widgetPalette } from "@/lib/colorTokens";

const BREATH_FULL = 100;

export function LivingOrganism(): JSX.Element {
  const vitals = useWsStore(selectVitals);
  const incarnation = useWsStore(selectIncarnationNumber);
  const terminal = useWsStore((s) => s.terminalLucidityEntered);
  const pal = widgetPalette("abyss");

  const breath = vitals?.breath ?? 0;
  const bankroll = vitals?.bankroll ?? 0;
  const state = terminal || breath <= 0 ? "TERMINAL" : breath <= 10 ? "DYING" : "ALIVE";
  const ringColor = state === "ALIVE" ? pal.accent : pal.danger;
  const pct = Math.max(0, Math.min(1, breath / BREATH_FULL));

  return (
    <div className="flex flex-col items-center gap-2" data-testid="living-organism">
      <div
        className="relative flex h-40 w-40 items-center justify-center rounded-full"
        style={{ border: `4px solid ${ringColor}`, boxShadow: state === "ALIVE" ? `0 0 28px ${ringColor}44` : "none" }}
      >
        <div className="absolute inset-[-4px] rounded-full"
             style={{ border: "4px solid transparent", borderTopColor: ringColor, borderRightColor: ringColor,
                      transform: `rotate(${pct * 360}deg)`, transition: "transform .6s ease" }} />
        <div className="text-center">
          <div className="text-2xl" style={{ color: ringColor }}>♥</div>
          <div className="font-mono text-lg" data-testid="organism-breath" style={{ color: pal.ink }}>{breath.toFixed(0)}</div>
          <div className="font-mono text-[9px]" style={{ color: pal.inkMuted }}>breath</div>
        </div>
      </div>
      <div className="font-mono text-xl" data-testid="organism-bankroll" style={{ color: pal.ink }}>
        ${bankroll.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
      </div>
      <div className="flex items-center gap-3 font-mono text-[10px]" style={{ color: pal.inkMuted }}>
        <span data-testid="organism-incarnation">Incarnation #{incarnation}</span>
        <span data-testid="organism-state" style={{ color: state === "ALIVE" ? pal.accent : pal.danger }}>● {state}</span>
      </div>
    </div>
  );
}
