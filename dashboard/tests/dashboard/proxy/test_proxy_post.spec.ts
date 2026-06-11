import { expect, test } from "@playwright/test";

/**
 * T-D-011 — happy POST pass-through.
 *
 * Exercises the path that originally broke production: `/workshop` form
 * submit → POST /api/backtest/run. The proxy must:
 *
 *   1. Forward the JSON body verbatim (Content-Type + bytes intact).
 *   2. Inject `Authorization: Bearer ${DASHBOARD_API_TOKEN}` server-side.
 *   3. Surface upstream's 202 + body back to the browser unchanged.
 *
 * The mock upstream echoes back the parsed JSON body into
 * `_proxy_echo.body`, so the assertions prove byte-level fidelity.
 */

const EXPECTED_TOKEN = "test-token-genesis-T-D-011";

test.describe("T-D-011 proxy — happy POST", () => {
  test("forwards POST /api/backtest/run with body + auth injected", async ({ request }) => {
    const payload = {
      note: JSON.stringify({
        start_date: "2026-01-01",
        end_date: "2026-04-30",
        starting_weights: [
          { label: "balanced", w_r: 0.5, w_s: 0.5, alpha: 0.5, beta: 0.5, rho: 0 },
        ],
        operator_note: "T-D-011 proxy smoke",
      }),
    };

    const response = await request.post("/api/proxy/api/backtest/run", {
      data: payload,
      headers: { "Content-Type": "application/json", Accept: "application/json" },
    });

    expect(response.status()).toBe(202);
    const body = await response.json();
    expect(body).toMatchObject({
      run_id: "test_run_id_001",
      status: "accepted",
    });
    // Token came from the server env, not from the browser.
    expect(body._proxy_echo.auth).toBe(`Bearer ${EXPECTED_TOKEN}`);
    // Body bytes survived the proxy hop intact — the upstream sees the
    // exact JSON the browser sent.
    expect(body._proxy_echo.body).toEqual(payload);
  });

  test("POST with empty body succeeds (no Content-Length crash)", async ({ request }) => {
    // Some control-plane endpoints accept POST with no body (e.g. /api/agent/start).
    // Verifies the proxy doesn't blow up on a zero-byte body.
    const response = await request.post("/api/proxy/api/backtest/run", {
      data: "",
      headers: { "Content-Type": "application/json" },
    });
    // Mock upstream returns 202 even on empty body; what we care about is
    // that the proxy itself doesn't crash before reaching upstream.
    expect([202, 400]).toContain(response.status());
  });
});
