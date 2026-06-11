import { expect, test } from "@playwright/test";

/**
 * T-D-011 — happy GET pass-through.
 *
 * The Next.js server-side proxy at `/api/proxy/[...path]` must:
 *
 *   1. Accept the GET, attach `Authorization: Bearer ${DASHBOARD_API_TOKEN}`
 *      from server-only env, and forward to `${DASHBOARD_API_URL}/<path>`.
 *   2. Pass the upstream JSON body back verbatim.
 *   3. Preserve query strings.
 *   4. NEVER carry the token across the browser hop — the request the
 *      Playwright `request` fixture sends has no Authorization header,
 *      yet the upstream sees one because the proxy injected it.
 *
 * Mock upstream echoes the Authorization header into `_proxy_echo.auth`
 * so the assertion below is end-to-end: we prove the token traveled from
 * the next-start env, through the proxy, to the upstream.
 */

const EXPECTED_TOKEN = "test-token-genesis-T-D-011";

test.describe("T-D-011 proxy — happy GET", () => {
  test("forwards GET /api/agent/status to upstream with bearer token injected", async ({ request }) => {
    const response = await request.get("/api/proxy/api/agent/status?foo=bar", {
      // Critically: the browser-side client sends NO Authorization header.
      // The proxy is the SOLE source of truth for the token.
      headers: { Accept: "application/json" },
    });
    expect(response.status()).toBe(200);
    const body = await response.json();
    expect(body).toMatchObject({
      phase: "PHASE_1_INFANCY",
      running: false,
    });
    // Mock upstream echoed the Authorization header it received → proves
    // the proxy injected the server-only token without the browser ever
    // seeing it.
    expect(body._proxy_echo.auth).toBe(`Bearer ${EXPECTED_TOKEN}`);
    // Query string survives the proxy hop.
    expect(body._proxy_echo.query).toBe("?foo=bar");
  });

  test("forwards GET /healthz so the User's curl smoke works", async ({ request }) => {
    const response = await request.get("/api/proxy/healthz");
    expect(response.status()).toBe(200);
    const body = await response.json();
    expect(body.ok).toBe(true);
    expect(body.auth).toBe(`Bearer ${EXPECTED_TOKEN}`);
  });

  test("pass-through preserves upstream 401 verbatim (no proxy short-circuit)", async ({ request }) => {
    // /__expect_401 is a mock-upstream endpoint that always returns 401.
    // The proxy must NOT swallow it as if its own token were missing —
    // upstream-401 and proxy-misconfigured-401 are different bugs and the
    // dashboard reacts differently to each.
    const response = await request.get("/api/proxy/__expect_401");
    expect(response.status()).toBe(401);
    const body = await response.json();
    expect(body.detail).toBe("unauthorized");
  });
});
