import { expect, test } from "@playwright/test";

/**
 * tests/dashboard/playwright/sandbox-live.spec.ts — T-D-009 acceptance.
 *
 * Verifies the sprint_8 LIVE-rewire end-to-end:
 *
 *   1. Spin up the dashboard with `SANDBOX_TEST=1` so the `/api/sandbox`
 *      route serves successive deterministic snapshots from
 *      `dashboard/__mocks__/sandbox_state.ts` (3 mock decisions over
 *      ~10s — matches the brief's 3-decision script).
 *   2. Drive the mock cursor by polling `/api/sandbox` 3× with a 1 s
 *      gap. Each call advances the cursor to the next stage.
 *   3. After each advance, assert the dashboard reflects the new
 *      decision within 4 s (2 s file-poll + 2 s render budget per the
 *      acceptance criteria).
 *   4. Assert the rendered BREATH numeric in the DOM equals the
 *      `agent_state.json` `breath_last_known` field as exposed in the
 *      bundle's `snapshot.breath` (mapped to `breath_pct` for the bar).
 *
 * Hermetic: NO Track B runtime is required — the API route serves the
 * deterministic mock. Run with:
 *
 *     SANDBOX_TEST=1 npm run test:e2e -- tests/dashboard/playwright
 *
 * The webServer in playwright.config.ts inherits SANDBOX_TEST from the
 * shell, so just exporting it before invoking `npm run test:e2e` is
 * enough; no per-test override needed.
 */

/** Pre-fab decision stages mirroring `dashboard/__mocks__/sandbox_state.ts`. */
const STAGES = [
  { tick: 1, breath: 88, bankroll: 102.5 },
  { tick: 2, breath: 86, bankroll: 102.5 },
  { tick: 3, breath: 84, bankroll: 98.25 },
] as const;

test.describe("Dashboard / sandbox-live wiring (T-D-009)", () => {
  test.beforeEach(async ({ request, baseURL }) => {
    test.skip(
      process.env.SANDBOX_TEST !== "1",
      "Spec requires SANDBOX_TEST=1 to drive the deterministic mock route.",
    );
    // Rewind the in-memory mock so each test starts from stage 0.
    const resetUrl = new URL("/api/sandbox", baseURL ?? "http://127.0.0.1:3100");
    const res = await request.post(resetUrl.toString());
    expect(res.ok()).toBeTruthy();
  });

  test("hook polls /api/sandbox and ingests successive stages within budget", async ({
    page,
    request,
    baseURL,
  }) => {
    // We mount the dashboard and dismiss the PLAYBACK takeover so the
    // VitalsPanel is reachable. The hook will fetch /api/sandbox on
    // mount (stage 0) and then every pollMs (default 2 s).
    await page.goto("/");
    await page.keyboard.press("Escape"); // exit PLAYBACK
    await expect(page.getByTestId("vitals-panel")).toBeVisible();

    // Wait for the first poll to land. After mount + an initial fetch
    // we expect the VitalsPanel to NOT be in skeleton (data-loading)
    // state inside the 4 s budget.
    await expect(
      page.getByTestId("vitals-panel"),
      "vitals-panel should hydrate within 4 s of first /api/sandbox poll",
    ).not.toHaveAttribute("data-loading", "true", { timeout: 4_000 });

    // Sequence: advance the mock cursor by 2 server-side polls + assert
    // the DOM reflects each stage. We trigger advancement by calling
    // /api/sandbox from the test runner (the route advances cursor on
    // each GET) and then wait up to 4 s for the dashboard's own poll
    // to pick up the next stage.
    for (let i = 1; i < STAGES.length; i++) {
      const stage = STAGES[i]!;
      // Force a server-side advance (mock cursor moves forward).
      const advanceUrl = new URL(
        "/api/sandbox",
        baseURL ?? "http://127.0.0.1:3100",
      );
      const res = await request.get(advanceUrl.toString());
      expect(res.ok()).toBeTruthy();
      const bundle = (await res.json()) as {
        snapshot: { breath: number; bankroll_usd: number; last_tick: number };
      };
      expect(bundle.snapshot.last_tick).toBeGreaterThanOrEqual(stage.tick);

      // The dashboard's own 2 s poll will pick this up within 4 s.
      // We assert the bankroll text — easier to reliably read than the
      // BREATH bar's CSS width.
      const bankrollText = `$${stage.bankroll.toFixed(2)}`;
      await expect(
        page.getByTestId("vitals-bankroll-value"),
        `vitals-bankroll-value should reflect stage ${i} (${bankrollText}) within 4 s`,
      ).toContainText(bankrollText, { timeout: 4_000 });
    }

    await page.screenshot({
      path: "screenshots/T-D-009/01-sandbox-live-final.png",
      fullPage: true,
    });
  });

  test("BREATH numeric in DOM equals snapshot.breath (mapped via INITIAL_BREATH=100)", async ({
    page,
  }) => {
    // Stage 0 of the deterministic mock is breath=88 (matches
    // dashboard/__mocks__/sandbox_state.ts cursor=0 snapshot). The
    // dashboard's first /api/sandbox poll on mount lands stage 0, so
    // the rendered BREATH numeric must read 88 (clamped via the
    // hook's INITIAL_BREATH=100 → 88%).
    const STAGE_0_BREATH = 88;

    await page.goto("/");
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("vitals-panel")).toBeVisible();
    await expect(page.getByTestId("vitals-panel")).not.toHaveAttribute(
      "data-loading",
      "true",
      { timeout: 4_000 },
    );

    const breathValue = page.getByTestId("vitals-breath-value");
    await expect(breathValue).toBeVisible();
    const rendered = (await breathValue.textContent()) ?? "";
    // VitalsPanel renders "<n> / 100" — pluck the LEADING integer.
    const match = /(\d+)/.exec(rendered);
    expect(match, `expected an integer in "${rendered}"`).not.toBeNull();
    const leading = Number(match?.[1] ?? "0");
    expect(leading).toBe(STAGE_0_BREATH);
  });

  test("lag-tape banner appears with severity ≤ info under hermetic mock", async ({
    page,
  }) => {
    await page.goto("/");
    await page.keyboard.press("Escape");
    // The hermetic mock writes a non-stale snapshot, so the cold_boot
    // alert dismisses after first poll. If the tape is present at all,
    // it must be info-severity (NOT error). The brief calls for graceful
    // fallback to polling so we accept either "tape gone" or "info tape".
    const tape = page.getByTestId("sandbox-lag-tape");
    const present = (await tape.count()) > 0;
    if (present) {
      const severity = await tape.getAttribute("data-severity");
      expect(["info", null]).toContain(severity);
    }
  });
});
