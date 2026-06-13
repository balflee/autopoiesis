/**
 * /reincarnation — Phase 2: the reincarnation experiment.
 *
 * THIN async server page (repo convention: no testable markup of its own —
 * the client shell carries everything). Reads the two gitignored artifacts
 * via the graceful loader; a missing numerical artifact degrades to a
 * "pending" note, a malformed one fails loudly.
 */

import type { JSX } from "react";

import { loadReincarnationOrNull } from "@/lib/load_reincarnation.server";
import type { ReincarnationFixture } from "@/lib/load_reincarnation";

import ReincarnationShell, { type ReincarnationArm } from "./ReincarnationShell";

// Generated artifacts are read fresh at request time (deploy-only files).
export const dynamic = "force-dynamic";

// A9 emergence-kit arms (MiniMax-M3), labelled for the toggle. Each is
// loaded gracefully — a missing arm simply drops from the toggle.
const A9_ARMS = [
  { mode: "g0", label: "G0 · kit-off LLM · ablation" },
  { mode: "g1", label: "G1 · full kit · treatment" },
  { mode: "g2", label: "G2 · shuffled season · falsification" },
] as const;

export default async function ReincarnationRoute(): Promise<JSX.Element> {
  let numerical: ReincarnationFixture | null = null;
  let armFixtures: (ReincarnationFixture | null)[] = [];
  let loadError: string | null = null;
  try {
    const loaded = await Promise.all([
      loadReincarnationOrNull({ mode: "numerical" }),
      ...A9_ARMS.map((a) => loadReincarnationOrNull({ mode: a.mode })),
    ]);
    numerical = loaded[0];
    armFixtures = loaded.slice(1);
  } catch (err) {
    loadError = err instanceof Error ? err.message : String(err);
  }

  if (numerical === null || loadError !== null) {
    return (
      <main className="abyss flex min-h-screen items-center justify-center px-6">
        <p className="max-w-xl text-center font-mono text-[11px] leading-relaxed text-[var(--ab-dim)]">
          {loadError !== null
            ? `the reincarnation artifact failed validation: ${loadError}`
            : "the phase-2 reincarnation run has not been generated yet — run `python scripts/run_reincarnation.py --provider numerical`."}
        </p>
      </main>
    );
  }

  const arms: ReincarnationArm[] = A9_ARMS.flatMap((a, i) => {
    const fixture = armFixtures[i];
    return fixture ? [{ key: a.mode, label: a.label, fixture }] : [];
  });

  return <ReincarnationShell numerical={numerical} arms={arms} />;
}
