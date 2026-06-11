"use client";

/**
 * MockLiveBody — the LIVE telemetry body of the /mock (Page 3) abyss page.
 *
 * This is the client island the server `page.tsx` mounts once the L5 gate is
 * open. It replicates the /live bootstrap chain so the reused widgets read from
 * the SAME global `useWsStore` singleton:
 *
 *   WsBootstrap            — opens the WS bridge (idle without env URLs) +
 *                            surfaces the DegradedFeedBanner.
 *   SandboxLiveBootstrap   — mounts the 2 s file-poll + SandboxStateProvider so
 *                            the file feed becomes the second writer.
 *
 * Every widget below is store-driven (no data props) and passed `variant="abyss"`
 * so the navy genesis palette is remapped onto the bioluminescent `--ab-*`
 * tokens — matching the rest of the abyss surface. The DeathWatch overlay is
 * global (fixed z-[70]); its red reconciles to ab-death under the abyss variant.
 *
 * NOTE: we mount ConsciousnessStream/LiveStream DIRECTLY (not the
 * ConsciousnessStream/index.tsx barrel) so the fixed inset-0 z-40 PLAYBACK
 * takeover does NOT hijack the /mock viewport — the live thought stream is an
 * inline panel here, not a full-screen takeover.
 */

import type { JSX } from "react";

import { DeathWatch } from "@/components/DeathWatch";
import { DecisionFeed } from "@/components/DecisionFeed";
import { DualEngineMeter } from "@/components/DualEngineMeter";
import { LiveStream } from "@/components/ConsciousnessStream/LiveStream";
import { SandboxLiveBootstrap } from "@/components/SandboxLiveBootstrap";
import { VitalsPanel } from "@/components/VitalsPanel";
import { WsBootstrap } from "@/components/WsBootstrap";

/** Small abyss section heading (mirrors the /backtest SectionHead motif). */
function SectionHead({
  index,
  kicker,
  title,
}: {
  index: string;
  kicker: string;
  title: string;
}): JSX.Element {
  return (
    <div className="flex flex-col gap-1">
      <span className="font-mono text-[10px] uppercase tracking-[0.32em] text-[var(--ab-dim)]">
        {index} · {kicker}
      </span>
      <h2 className="font-display text-3xl italic text-[var(--ab-text)] sm:text-4xl">
        {title}
      </h2>
    </div>
  );
}

export function MockLiveBody(): JSX.Element {
  return (
    <WsBootstrap>
      <SandboxLiveBootstrap>
        {/* Global takeover surface — reconciled to ab-death under abyss. */}
        <DeathWatch variant="abyss" />

        <div className="flex flex-col gap-12" data-testid="mock-live-body">
          {/* ---- 01 · VITALS -------------------------------------- */}
          <section
            data-testid="mock-vitals-section"
            className="ab-reveal flex flex-col gap-5"
            style={{ animationDelay: "120ms" }}
          >
            <SectionHead index="01" kicker="live vitals" title="The breath, right now" />
            <VitalsPanel variant="abyss" />
          </section>

          {/* ---- 02 · MIND ---------------------------------------- */}
          <section
            data-testid="mock-mind-section"
            className="ab-reveal flex flex-col gap-5"
            style={{ animationDelay: "200ms" }}
          >
            <SectionHead index="02" kicker="the two engines" title="What it is thinking" />
            <div className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
              <DualEngineMeter variant="abyss" />
              <LiveStream variant="abyss" showPlaybackHint={false} />
            </div>
          </section>

          {/* ---- 03 · DECISIONS ----------------------------------- */}
          <section
            data-testid="mock-decisions-section"
            className="ab-reveal flex flex-col gap-5"
            style={{ animationDelay: "280ms" }}
          >
            <SectionHead
              index="03"
              kicker="what + why"
              title="Every paper bet, and the read behind it"
            />
            <p className="max-w-2xl font-mono text-[11px] leading-relaxed text-[var(--ab-dim)]">
              Each row is a real mock-order against live Polymarket prices — the
              market it evaluated, the side and size it took, and (expanded) the
              five per-engine signal scores that drove the call.
            </p>
            <DecisionFeed variant="abyss" />
          </section>
        </div>
      </SandboxLiveBootstrap>
    </WsBootstrap>
  );
}

export default MockLiveBody;
