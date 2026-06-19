"use client";

import { type JSX } from "react";

import { WsBootstrap } from "@/components/WsBootstrap";
import { SandboxLiveBootstrap } from "@/components/SandboxLiveBootstrap";
import { DeathWatch } from "@/components/DeathWatch";
import { LivingOrganism } from "@/components/living/LivingOrganism";
import { DivineEventStream } from "@/components/living/DivineEventStream";
import { DivineTreasury } from "@/components/living/DivineTreasury";
import { CurrentMarketCard } from "@/components/living/CurrentMarketCard";
import { OpenPositions } from "@/components/living/OpenPositions";
import { FusionSignalsRail } from "@/components/living/FusionSignalsRail";
import { IncarnationLineage } from "@/components/living/IncarnationLineage";

/**
 * Living Stage (Layout A) — one continuous live stage: the organism + the bet
 * it is making right now (center), the Gods (left rail), the Mind (right rail),
 * and the reincarnation lineage (bottom). All zones read the live wsStore that
 * the SandboxLiveBootstrap 2 s poll feeds; no props for live data.
 *
 * Mirrors the /mock bootstrap chain (WsBootstrap → SandboxLiveBootstrap), so
 * the divine-economy data (gods_treasury/deaths + decision odds/signals) flows
 * through the exact same poll path the rest of the sandbox UI uses.
 */
export function LivingStageBody(): JSX.Element {
  return (
    <WsBootstrap>
      <SandboxLiveBootstrap>
        {/* Global takeover surface — reconciled to ab-death under abyss. */}
        <DeathWatch variant="abyss" />

        <div className="flex flex-col gap-4" data-testid="living-stage-body">
          <div
            className="flex items-center justify-between font-mono text-[10px]"
            style={{ color: "var(--ab-dim)" }}
          >
            <span style={{ color: "var(--ab-glow)" }}>◆ AUTOPOIESIS</span>
            <span>live mock-bet · Polymarket tennis · paper-traded, real odds</span>
          </div>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-[23%_minmax(0,1fr)_25%]">
            <aside className="flex flex-col gap-3">
              <DivineEventStream />
              <DivineTreasury />
            </aside>
            <section className="flex flex-col items-center gap-4">
              <LivingOrganism />
              <CurrentMarketCard />
              <OpenPositions />
            </section>
            <aside>
              <FusionSignalsRail />
            </aside>
          </div>

          <IncarnationLineage />
        </div>
      </SandboxLiveBootstrap>
    </WsBootstrap>
  );
}
