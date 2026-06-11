"use client";

import { PHASE2_DAY4_SNAPSHOT } from "@/lib/memoryBank";

import { LiveStream } from "./LiveStream";
import { PlaybackTakeover } from "./PlaybackTakeover";
import { usePlaybackController } from "./usePlaybackController";

/**
 * ConsciousnessStream — the central narrative surface of the dashboard.
 *
 * Sprint_1 (T-D-001) shipped PLAYBACK ONLY. T-D-002 fills in the LIVE
 * branch: instead of a passive stub it now renders {@link LiveStream},
 * a typewriter view of WS `thought` frames driven by the global
 * {@link useWsStore}. PLAYBACK still wins by default so the demo flow
 * (Phase 2 Day 4 first-Twitter-mistake) plays automatically on load.
 *
 * During PLAYBACK the takeover is wrapped in `fixed inset-0 z-40` so
 * it covers the VitalsPanel + DualEngineMeter grid that the dashboard
 * page mounts around it. After Esc, LiveStream renders inline as a
 * normal panel inside the grid.
 */
export function ConsciousnessStream(): JSX.Element {
  const controller = usePlaybackController({
    snapshot: PHASE2_DAY4_SNAPSHOT,
    initialMode: "PLAYBACK",
    autoplay: true,
  });

  if (controller.mode === "PLAYBACK") {
    return (
      <div className="fixed inset-0 z-40">
        <PlaybackTakeover
          snapshot={PHASE2_DAY4_SNAPSHOT}
          controller={controller}
        />
      </div>
    );
  }

  return <LiveStream />;
}

export default ConsciousnessStream;
