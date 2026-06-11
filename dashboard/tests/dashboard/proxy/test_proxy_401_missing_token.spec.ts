import { expect, test } from "@playwright/test";

/**
 * T-D-011 — proxy returns 401 when DASHBOARD_API_TOKEN is missing.
 *
 * The acceptance criterion: if Vercel admin forgets to set
 * DASHBOARD_API_TOKEN in production env, the proxy MUST short-circuit
 * with 401 + a clear detail message, rather than silently forward an
 * un-authenticated request to the upstream (which would return its own
 * 401, leaving the operator unable to distinguish "proxy misconfigured"
 * from "backend rejected token").
 *
 * We can't restart `next start` mid-test to unset the env, so the proxy
 * exposes a test-only seam (gated by `PROXY_TEST_MODE === "1"`) that
 * lets the test simulate the missing-token branch via a request header.
 * The seam is BLOCKED on production deploys where `PROXY_TEST_MODE` is
 * unset — see `dashboard/app/api/proxy/[...path]/route.ts` for the guard.
 */

test.describe("T-D-011 proxy — 401 when server token missing", () => {
  test("returns 401 with a clear detail when the test seam clears the token", async ({ request }) => {
    const response = await request.get("/api/proxy/healthz", {
      headers: { "x-genesis-proxy-test-clear-token": "1" },
    });
    expect(response.status()).toBe(401);
    const body = await response.json();
    expect(body.detail).toMatch(/DASHBOARD_API_TOKEN.*not set/i);
  });

  test("the 401 short-circuits before the upstream is contacted", async ({ request }) => {
    // The proxy must NOT forward when its own token guard fails — otherwise
    // an unauthenticated request would leak out + the mock upstream's
    // EXPECTED_TOKEN guard would respond with its own 401, masking the
    // proxy-config bug. We assert the 401 message comes from the PROXY
    // (mentions `DASHBOARD_API_TOKEN`), not from the mock upstream
    // (which would say `bad token` / `unauthorized`).
    const response = await request.post("/api/proxy/api/backtest/run", {
      data: { note: "should never reach upstream" },
      headers: {
        "Content-Type": "application/json",
        "x-genesis-proxy-test-clear-token": "1",
      },
    });
    expect(response.status()).toBe(401);
    const body = await response.json();
    expect(body.detail).toContain("DASHBOARD_API_TOKEN");
  });

  test("without the test seam, requests succeed (sanity baseline)", async ({ request }) => {
    // Verifies that the seam is the ONLY trigger for the 401 — otherwise
    // the test "passes" trivially because all requests are 401'd.
    const response = await request.get("/api/proxy/healthz");
    expect(response.status()).toBe(200);
  });
});
