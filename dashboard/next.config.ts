import type { NextConfig } from "next";

/**
 * Genesis Dashboard — Next.js 15 (App Router).
 *
 * The PLAYBACK widget for T-D-001 imports the snapshot JSON as a static
 * module — no network access. We keep the config minimal so the demo
 * stays deterministic. LIVE / WebSocket bridge is sprint_2+ scope.
 */
const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  productionBrowserSourceMaps: false,
  // We deliberately do NOT set rewrites/redirects/headers — the dashboard
  // is a single-page demo surface. Any cross-track contract changes flow
  // through .dev/contracts/, not Next.js config.
  experimental: {
    typedRoutes: true,
  },
  // The /survival route is `force-dynamic` and reads the large survival-journey
  // artifacts from `public/backtest/*.json` via `fs.readFile` at REQUEST time.
  // On Vercel, `public/` is CDN-served but NOT in the serverless function's
  // filesystem, so the read would ENOENT and the page would show "not
  // generated". Trace the artifacts INTO the /survival function bundle so the
  // runtime read resolves. (Next 15: top-level key, not under `experimental`.)
  outputFileTracingIncludes: {
    "/survival": [
      "./public/backtest/survival_journey.json",
      "./public/backtest/survival_journey_ai.json",
      // Archived pre-realism-rules snapshots (finetune-log exhibits). The
      // loader degrades gracefully when absent, so a checkout without them
      // still builds + serves.
      "./public/backtest/survival_journey_run1.json",
      "./public/backtest/survival_journey_ai_run1.json",
      // Provider-comparison leg (Gemini-only full run, same rules).
      "./public/backtest/survival_journey_ai_gemini.json",
    ],
  },
};

export default nextConfig;
