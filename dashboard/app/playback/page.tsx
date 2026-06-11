import { redirect } from "next/navigation";

/**
 * /playback — LEGACY dev surface, folded into the lifeline (TASK G2).
 *
 * The full-surface PLAYBACK route (curated demo arc) is a navy/dev view,
 * not part of the ABYSS lifeline (roadmap · backtest · survival · mock).
 * To keep a single canonical set of surfaces, any stale link or bookmark
 * to `/playback` now redirects to the lifeline hub at `/roadmap` —
 * mirroring the site root at `app/page.tsx`.
 *
 * The former implementation (PlaybackPanel against the Phase-2 Day-4
 * fixture) remains in git history if playback is ever resurrected as its
 * own surface.
 */
export default function PlaybackRoute(): never {
  redirect("/roadmap");
}
