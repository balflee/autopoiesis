/**
 * /mock — Phase F showpiece (F2): the LIVE mock-bet "Adult" stage.
 *
 * The agent has survived L5 (the /survival page) and now paper-trades against
 * LIVE Polymarket prices with real mock orders — the same telemetry surface as
 * /live, but told in the `.abyss` design system and gated behind the L5
 * lifecycle so the lifeline reads in order.
 *
 * Server component. The whole route is guarded by F1's readL5Complete()
 * (NEXT_PUBLIC_L5_COMPLETE). Until L5 is complete the page renders a
 * survival-style locked empty-state (data-testid `mock-locked`) and mounts NONE
 * of the live widgets — so a deep-link to /mock is blocked too, mirroring the
 * roadmap node staying LOCKED.
 *
 * Once unlocked, the body is the {@link MockLiveBody} client island: it
 * replicates the /live bootstrap chain (WsBootstrap → SandboxLiveBootstrap) so
 * the reused widgets read the SAME global useWsStore singleton, each skinned
 * with `variant="abyss"`.
 *
 * Aesthetic: the `.abyss` design system in `app/globals.css`, matching the
 * roadmap landing + /backtest + /survival. No shared shell component exists yet
 * (each abyss page copy-pastes the shell); the local {@link Shell} mirrors the
 * survival/page.tsx helper.
 */

import Link from "next/link";
import type { JSX, ReactNode } from "react";

import { readL5Complete } from "@/lib/l5_gate";
import { STAGE_META } from "@/lib/lifeline";
import {
  BackLinkDim,
  LifelineFooter,
  StageShell,
} from "@/components/lifeline/StageShell";

import { MockLiveBody } from "./MockLiveBody";

/**
 * Thin wrapper over the shared {@link StageShell} pinning the mock stage
 * metadata, so both the locked empty-state (no footer) and the unlocked live
 * body (footer) render through the SAME shell.
 */
function Shell({
  children,
  footer,
}: {
  children: ReactNode;
  footer?: ReactNode;
}): JSX.Element {
  return (
    <StageShell meta={STAGE_META.mock} footer={footer}>
      {children}
    </StageShell>
  );
}

export default function MockRoute(): JSX.Element {
  // F1 gate — fails closed. Until L5 is complete the route stays locked and the
  // live widgets never mount (deep-link blocked).
  if (!readL5Complete()) {
    return (
      <Shell>
        <section
          data-testid="mock-locked"
          className="rounded-xl border border-[var(--ab-moss)]/30 bg-[var(--ab-bg-2)]/60 p-6"
        >
          <p className="font-mono text-[11px] uppercase tracking-[0.22em] text-[var(--ab-death)]">
            mock-bet stage locked
          </p>
          <p className="mt-3 font-mono text-[11px] leading-relaxed text-[var(--ab-dim)]">
            The agent is still in its{" "}
            <Link
              href="/survival"
              className="text-[var(--ab-text)] underline decoration-[var(--ab-moss)] underline-offset-2 transition-colors hover:text-[var(--ab-glow)]"
            >
              L5 survival season
            </Link>
            . The live mock-bet surface unlocks only once L5 · LEARNING has
            completed — until then this stage of the lifeline stays sealed.
          </p>
          <p className="mt-3 font-mono text-[10px] leading-relaxed text-[var(--ab-dim)]/70">
            (gate: NEXT_PUBLIC_L5_COMPLETE)
          </p>
        </section>
      </Shell>
    );
  }

  return (
    <Shell
      footer={
        <LifelineFooter
          note="adult · live mock-bet · reusing the /live telemetry stack"
          nav={
            // Backward lifeline edge → DIM accent, matching survival's
            // "◂ back to the seed" (BackLinkDim). Forward edges use the glow
            // NextLink; this one points back to /survival so it stays dim.
            <BackLinkDim
              href={STAGE_META.survival.href}
              ariaLabel="Back to the survival stage"
            >
              ◂ back to survival
            </BackLinkDim>
          }
        />
      }
    >
      <MockLiveBody />
    </Shell>
  );
}
