"use client";
import { useWsStore, selectDivineEvents } from "@/lib/wsStore";
import { widgetPalette } from "@/lib/colorTokens";
import type { GodsTreasuryRecordData } from "@/lib/sandbox_state_shared";

function EventCard({
  ev,
  pal,
}: {
  ev: GodsTreasuryRecordData;
  pal: ReturnType<typeof widgetPalette>;
}): JSX.Element {
  if (ev.type === "tithe") {
    const cost =
      ev.paid_usd > 0
        ? `− $${ev.paid_usd.toFixed(2)}`
        : `− ${ev.breath_cost.toFixed(0)} breath`;
    return (
      <div
        className="rounded border-l-2 px-2 py-1"
        style={{ borderColor: pal.danger, background: `${pal.danger}14` }}
      >
        <div className="font-mono text-[9px]" style={{ color: pal.danger }}>
          TITHE · the rent
        </div>
        <div className="font-mono text-[11px]" style={{ color: pal.ink }}>
          {cost}
        </div>
      </div>
    );
  }
  return (
    <div
      className="rounded border-l-2 px-2 py-1"
      style={{ borderColor: pal.accent, background: `${pal.accent}14` }}
    >
      <div className="font-mono text-[9px]" style={{ color: pal.accent }}>
        TRIBUTE · deathbed
      </div>
      <div className="font-mono text-[11px]" style={{ color: pal.ink }}>
        ${ev.amount_usd.toFixed(0)}
        {ev.dice_roll != null ? ` → dice ${ev.dice_roll.toFixed(2)}` : ""}
      </div>
      <div
        className="font-mono text-[9px]"
        style={{ color: ev.success ? pal.accent : pal.danger }}
      >
        {ev.success ? "▲ SURVIVED" : "✝ REFUSED"}
      </div>
    </div>
  );
}

export function DivineEventStream(): JSX.Element {
  const events = useWsStore(selectDivineEvents);
  const pal = widgetPalette("abyss");
  const newestFirst = [...events].reverse();
  return (
    <div className="flex flex-col gap-1.5" data-testid="divine-event-stream">
      <div
        className="font-mono text-[9px] uppercase tracking-[0.18em]"
        style={{ color: pal.danger }}
      >
        ⛧ The Gods
      </div>
      {newestFirst.length === 0 ? (
        <div className="font-mono text-[9px]" style={{ color: pal.inkMuted }}>
          the gods are quiet…
        </div>
      ) : (
        newestFirst.map((ev) => (
          <EventCard
            key={ev.type === "tithe" ? ev.tithe_id : ev.tribute_id}
            ev={ev}
            pal={pal}
          />
        ))
      )}
    </div>
  );
}
