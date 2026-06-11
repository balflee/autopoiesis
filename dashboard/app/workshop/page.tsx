import { redirect } from "next/navigation";

/**
 * /workshop — LEGACY dev surface, folded into the lifeline (TASK G2).
 *
 * The backtest-sweep configurator that lived here is a navy/dev view, not
 * part of the ABYSS lifeline (roadmap · backtest · survival · mock). To
 * keep a single canonical set of surfaces, any stale link or bookmark to
 * `/workshop` now redirects to the lifeline hub at `/roadmap` — mirroring
 * the site root at `app/page.tsx`.
 *
 * The former implementation remains in git history if the sweep console
 * is ever resurrected as its own surface.
 */
export default function WorkshopPage(): never {
  redirect("/roadmap");
}
