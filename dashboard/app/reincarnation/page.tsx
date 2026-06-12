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

import ReincarnationShell from "./ReincarnationShell";

// Generated artifacts are read fresh at request time (deploy-only files).
export const dynamic = "force-dynamic";

export default async function ReincarnationRoute(): Promise<JSX.Element> {
  let numerical: ReincarnationFixture | null = null;
  let ai: ReincarnationFixture | null = null;
  let loadError: string | null = null;
  try {
    [numerical, ai] = await Promise.all([
      loadReincarnationOrNull({ mode: "numerical" }),
      loadReincarnationOrNull({ mode: "ai" }),
    ]);
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

  return <ReincarnationShell numerical={numerical} ai={ai} />;
}
