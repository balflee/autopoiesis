import { AgentControls } from "@/components/AgentControls";
import { ConsciousnessStream } from "@/components/ConsciousnessStream";
import { DeathWatch } from "@/components/DeathWatch";
import { DeathWatchBorder } from "@/components/death_watch/DeathWatchBorder";
import { DecisionFeed } from "@/components/DecisionFeed";
import { DualEngineMeter } from "@/components/DualEngineMeter";
import { EvolutionCurve } from "@/components/EvolutionCurve";
import { LLMActivationOverlay } from "@/components/LLMActivationOverlay";
import { PhaseTransitionBanner } from "@/components/PhaseTransitionBanner";
import { PlaybackMode } from "@/components/PlaybackMode";
import { ProposalReview } from "@/components/ProposalReview";
import { ReflectionFeed } from "@/components/ReflectionFeed";
import { SandboxLiveBootstrap } from "@/components/SandboxLiveBootstrap";
import { VitalsPanel } from "@/components/VitalsPanel";
import Link from "next/link";
import { WsBootstrap } from "@/components/WsBootstrap";

/**
 * /live — the live agent dashboard.
 *
 * NOTE (Phase C redesign): this is the former site root (`app/page.tsx`).
 * The root now lands on the Roadmap lifeline (`/roadmap`); the live
 * telemetry surface moved here unchanged so the redesign is additive and
 * the existing WS/sandbox wiring + widgets keep working untouched.
 *
 * The dashboard is organised into three vertical bands:
 *
 *   1. TOP   — DegradedFeedBanner (auto) + PhaseTransitionBanner +
 *              VitalsPanel
 *   2. MID   — DualEngineMeter | ConsciousnessStream
 *   3. BOT   — EvolutionCurve | DecisionFeed
 *
 * Plus two overlay surfaces that mount globally:
 *   - LLMActivationOverlay (one-shot at β₁ unfreeze)
 *   - PLAYBACK takeover inside ConsciousnessStream (Phase 2 Day 4)
 */
export default function LiveDashboardPage(): JSX.Element {
  return (
    <main className="relative min-h-[100dvh] w-full bg-genesis-bg text-genesis-ink">
      <WsBootstrap>
        <SandboxLiveBootstrap>
          <PlaybackMode />
          <PhaseTransitionBanner />
          <LLMActivationOverlay />
          {/* DeathWatchBorder: T-D-007 — early-warning pulsing red ring
              that mounts as soon as BREATH dips below the configured
              threshold (default 10 % per PRD §8). Layered ABOVE the dash
              body content but BELOW the full DeathWatch takeover surface. */}
          <DeathWatchBorder />
          <DeathWatch />
          <div className="mx-auto flex w-full max-w-6xl flex-col gap-4 p-4 sm:p-6">
            {/* T-D-010 sprint_9 — header AgentControls bar (status pill +
                BREATH live ticker + start/stop). Sits above VitalsPanel. */}
            <AgentControls />
            <nav
              data-testid="dashboard-route-links"
              className="flex flex-wrap items-baseline gap-2 font-mono text-[10px] uppercase tracking-[0.28em] text-genesis-ink-muted"
            >
              <span>routes ·</span>
              <Link
                href="/roadmap"
                className="rounded-sm border border-genesis-ink-muted/30 px-2 py-0.5 transition-colors hover:border-genesis-amber/70 hover:text-genesis-amber focus:outline-none focus-visible:ring-2 focus-visible:ring-genesis-amber/70"
              >
                roadmap ▸
              </Link>
              <Link
                href="/backtest"
                className="rounded-sm border border-genesis-ink-muted/30 px-2 py-0.5 transition-colors hover:border-genesis-amber/70 hover:text-genesis-amber focus:outline-none focus-visible:ring-2 focus-visible:ring-genesis-amber/70"
              >
                backtest ▸
              </Link>
            </nav>
            <VitalsPanel />
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
              <DualEngineMeter />
              <ConsciousnessStream />
            </div>
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
              <EvolutionCurve />
              <DecisionFeed />
            </div>
            {/* T-D-010 sprint_9 — L3 reflection stream + proposal review
                row. ReflectionFeed reads SSE event:reflections; ProposalReview
                stays in empty-state until sprint_10 lands the LLM advisor. */}
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
              <ReflectionFeed />
              <ProposalReview />
            </div>
          </div>
        </SandboxLiveBootstrap>
      </WsBootstrap>
    </main>
  );
}
