import { expect, test } from "@playwright/test";

/**
 * Live post-deploy smoke — T-D-013 sprint_10 (Day 6).
 *
 * Two minimal smoke tests that target the LIVE Vercel deploy, NOT the
 * hermetic in-tree `next start` that the rest of the suite mounts.
 * Purpose: prove that
 *
 *   (1) the same-origin proxy (`/api/proxy/healthz`) reaches the Railway
 *       FastAPI backend with the server-injected bearer token, and
 *   (2) the lifeline surfaces are healthy on the live deploy: the four
 *       ABYSS lifeline pages (/roadmap, /backtest, /survival, /mock)
 *       return 200, and the folded legacy routes (/workshop, /playback)
 *       redirect to /roadmap — so we catch a broken bundle on Vercel
 *       BEFORE the demo.
 *
 * G2: the old "(2) /workshop renders its sweep-config form chrome" check
 * is gone — /workshop + /playback were folded into the lifeline and now
 * 307/308-redirect to /roadmap (see app/workshop/page.tsx,
 * app/playback/page.tsx). This spec now asserts that redirect behaviour
 * plus the live lifeline pages' 200s, reflecting reality.
 *
 * Why env-gated (`PLAYWRIGHT_LIVE=1`) instead of always-on:
 *
 *   - The Playwright config boots a local `next start` on port 3100 as
 *     part of its `webServers` array. The default `baseURL` in
 *     `playwright.config.ts` points at `http://127.0.0.1:3100`, so any
 *     spec that uses `page.goto(...)` with a relative path hits the
 *     local server, not Vercel. We bypass that by using absolute URLs
 *     against `LIVE_DASHBOARD_URL` and skipping the entire suite when
 *     `PLAYWRIGHT_LIVE !== "1"`, so default CI keeps running fast,
 *     hermetic, network-free.
 *   - The brief explicitly calls out:
 *       "Marked `@live`; skipped in default CI (env-gated
 *        `PLAYWRIGHT_LIVE=1`)."
 *
 * How to run manually (the only way this fires today):
 *
 *   PLAYWRIGHT_LIVE=1 \
 *   LIVE_DASHBOARD_URL=https://autopoiesis-six.vercel.app \
 *   npm run test:e2e -- tests/dashboard/e2e/test_live_smoke_post_deploy.spec.ts
 *
 * The `@live` annotation is repeated in `test.describe.skip` / `test()`
 * titles so a grep-by-title harness can shortcut without parsing
 * metadata.
 *
 * What this spec INTENTIONALLY does NOT do:
 *
 *   - It does NOT POST `/api/proxy/api/proposals/{id}/approve`. The
 *     approve endpoint is exercised manually via curl (see
 *     `delivery_report.md`) because firing a side-effecting POST against
 *     a live proposal would race the deterministic-test contract that
 *     T-B-032 just locked in. The brief's acceptance criteria require a
 *     live POST evidence line in the delivery report; the spec keeps the
 *     GET-only floor.
 *   - It does NOT crawl `/` or any other route beyond the proxy health
 *     check, the four lifeline pages, and the two folded legacy
 *     redirects. Adding more surfaces would conflate this smoke with the
 *     broader /qa suite.
 */

const LIVE_URL = process.env.LIVE_DASHBOARD_URL ?? "https://autopoiesis-six.vercel.app";
const LIVE_ENABLED = process.env.PLAYWRIGHT_LIVE === "1";

test.describe("@live post-deploy smoke (env-gated PLAYWRIGHT_LIVE=1)", () => {
  test.skip(
    !LIVE_ENABLED,
    "PLAYWRIGHT_LIVE !== '1' — live deploy smoke disabled in default CI.",
  );

  test("@live /api/proxy/healthz reaches the Railway backend through the server-side proxy", async ({
    request,
  }) => {
    // Hits Vercel; Vercel attaches `Authorization: Bearer
    // ${DASHBOARD_API_TOKEN}` server-side and forwards to
    // `${DASHBOARD_API_URL}/healthz`. A 200 with a JSON body proves both
    // the proxy is deployed AND the upstream is up.
    const res = await request.get(`${LIVE_URL}/api/proxy/healthz`, {
      timeout: 15_000,
    });
    expect(
      res.status(),
      `expected 200 from ${LIVE_URL}/api/proxy/healthz; got ${res.status()}`,
    ).toBe(200);

    const ct = res.headers()["content-type"] ?? "";
    expect(
      ct,
      `expected JSON content-type from /healthz proxy; got ${ct}`,
    ).toMatch(/application\/json/);

    // FastAPI's `/healthz` returns `{"status": "ok"}` (or similar). We
    // assert on a stable subset so a backend schema bump doesn't flake.
    const body = await res.json();
    expect(body, "/healthz body should be an object").toBeTruthy();
    expect(typeof body).toBe("object");
  });

  // G2 — the folded legacy routes must redirect into the lifeline hub.
  // Each is a server component calling redirect('/roadmap'), so a live GET
  // either reports a 307/308 status OR (when Playwright auto-follows the
  // redirect) lands on /roadmap. We accept either signal so the assertion
  // is robust to redirect-following.
  for (const legacy of ["/workshop", "/playback"]) {
    test(`@live ${legacy} redirects into the lifeline at /roadmap`, async ({
      page,
    }) => {
      const res = await page.goto(`${LIVE_URL}${legacy}`, {
        waitUntil: "domcontentloaded",
        timeout: 30_000,
      });

      const status = res?.status();
      const landedOnRoadmap = new URL(page.url()).pathname === "/roadmap";
      const isRedirectStatus = status === 307 || status === 308;

      expect(
        isRedirectStatus || landedOnRoadmap,
        `GET ${legacy} should 307/308 to /roadmap or land on /roadmap; got status ${status} at ${page.url()}`,
      ).toBe(true);
    });
  }

  // G2 — the four ABYSS lifeline pages must each serve a 200 on the live
  // deploy so a broken bundle is caught before the demo. (/mock may render
  // its locked empty-state behind the L5 gate — that is still a 200.)
  for (const page_path of ["/roadmap", "/backtest", "/survival", "/mock"]) {
    test(`@live ${page_path} returns 200`, async ({ page }) => {
      const consoleErrors: string[] = [];
      page.on("console", (msg) => {
        if (msg.type() === "error") {
          consoleErrors.push(msg.text());
        }
      });
      page.on("pageerror", (err) => consoleErrors.push(err.message));

      const res = await page.goto(`${LIVE_URL}${page_path}`, {
        waitUntil: "domcontentloaded",
        timeout: 30_000,
      });
      expect(
        res?.status(),
        `GET ${page_path} should return 200`,
      ).toBe(200);

      expect(
        consoleErrors,
        `Vercel deploy emitted browser console errors on ${page_path}: ${consoleErrors.join(
          " | ",
        )}`,
      ).toEqual([]);
    });
  }
});
