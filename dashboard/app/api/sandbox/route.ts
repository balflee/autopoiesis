/**
 * dashboard/app/api/sandbox/route.ts — server-side bridge between the
 * browser hook and `state/sandbox/`.
 *
 * Why an API route (not a server component):
 *   - The dashboard's `/` route is interactive and needs to poll every
 *     2 s. Re-rendering a server component on each poll would force
 *     either a full route refresh (jarring) or an RSC streaming setup
 *     (overengineered for the demo surface).
 *   - The route is the SINGLE place that touches `node:fs`. Client
 *     code never sees Node built-ins.
 *
 * Test gate:
 *   - When `SANDBOX_TEST=1` is set, this route serves successive
 *     deterministic snapshots from `dashboard/__mocks__/sandbox_state.ts`
 *     instead of reading the filesystem. The Playwright spec at
 *     `dashboard/tests/dashboard/playwright/sandbox-live.spec.ts` uses
 *     this gate to run hermetically.
 *
 * No-cache: `dynamic = "force-dynamic"` + `revalidate = 0` so Next.js
 * does NOT cache a snapshot between polls. The brief calls for "within
 * 4s of file write", and caching the response would break that.
 */

import { NextResponse } from "next/server";

import {
  loadSandboxBundle,
  loadSandboxBundleFromBackend,
} from "@/lib/load_sandbox_state.server";

export const dynamic = "force-dynamic";
export const revalidate = 0;
// Forwarding to the backend (raw state fetch) needs the Node fetch +
// process.env reads; pin the route to the Node runtime so the
// server-only token is never edge-bundled. Mirrors /api/proxy.
export const runtime = "nodejs";

/**
 * GET /api/sandbox — returns a {@link SandboxStateBundle}.
 *
 * The response is always 200 with a populated `lag_alerts` array; we
 * never 5xx here because the dashboard needs to keep painting even
 * when the writer hasn't started.
 */
export async function GET(): Promise<NextResponse> {
  if (process.env.SANDBOX_TEST === "1") {
    // Lazy-import so production builds tree-shake the mock.
    const mock = await import("@/__mocks__/sandbox_state");
    return NextResponse.json(mock.nextMockTick(), {
      headers: { "Cache-Control": "no-store" },
    });
  }
  // Data path selection:
  //   - DASHBOARD_API_URL set (Vercel `/living`, backend on Railway) →
  //     fetch the raw state from the backend and fold it. The local fs
  //     here is NOT the loop's volume, so reading it would show nothing.
  //   - else (local dev / co-located deploy) → read `state/sandbox/`
  //     directly off disk. Byte-identical to the pre-rewire behaviour.
  // Both paths fold via the SAME `foldSandboxBundle`.
  const apiBase = process.env.DASHBOARD_API_URL;
  const bundle =
    apiBase && apiBase.length > 0
      ? await loadSandboxBundleFromBackend()
      : await loadSandboxBundle();
  return NextResponse.json(bundle, {
    headers: { "Cache-Control": "no-store" },
  });
}

/**
 * POST /api/sandbox/reset — test-only seam to rewind the mock cursor.
 *
 * Disabled when `SANDBOX_TEST !== "1"`. The Playwright spec calls this
 * in `beforeEach` so each test starts from a known stage.
 */
export async function POST(): Promise<NextResponse> {
  if (process.env.SANDBOX_TEST !== "1") {
    return NextResponse.json({ ok: false }, { status: 403 });
  }
  const mock = await import("@/__mocks__/sandbox_state");
  mock.resetMockSandbox();
  return NextResponse.json({ ok: true });
}
