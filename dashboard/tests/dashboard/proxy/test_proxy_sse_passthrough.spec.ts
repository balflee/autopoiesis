import { expect, test } from "@playwright/test";

/**
 * T-D-011 — SSE pass-through.
 *
 * Two acceptance criteria from the brief:
 *
 *   1. Proxy preserves `Content-Type: text/event-stream` so the browser's
 *      EventSource consumes the response as SSE rather than buffered JSON.
 *   2. Upstream events flow through the proxy unchanged — the mock emits
 *      a `decisions` event with a known payload, and we observe that
 *      payload on the client side.
 *
 * Implementation note: Playwright's `request` fixture buffers the entire
 * response body before returning. For an open-ended SSE this is fine for
 * our mock (it closes after 250 ms emitting two events). We assert on
 * the buffered body to confirm pass-through fidelity; the live browser
 * `EventSource` path is exercised by the existing test_sse_auth_blocked
 * spec which mocks the proxy URL via `page.route`.
 */

const EXPECTED_TOKEN = "test-token-genesis-T-D-011";

test.describe("T-D-011 proxy — SSE pass-through", () => {
  test("preserves text/event-stream content-type", async ({ request }) => {
    const response = await request.get("/api/proxy/api/state/stream", {
      headers: { Accept: "text/event-stream" },
    });
    expect(response.status()).toBe(200);
    const contentType = response.headers()["content-type"] ?? "";
    expect(contentType).toContain("text/event-stream");
  });

  test("forwards SSE event frames byte-for-byte from upstream", async ({ request }) => {
    const response = await request.get("/api/proxy/api/state/stream", {
      headers: { Accept: "text/event-stream" },
    });
    expect(response.status()).toBe(200);
    const wire = await response.text();

    // Mock upstream emits exactly two `decisions` events. The SSE wire
    // protocol uses `event: <name>\ndata: <json>\n\n` framing — the proxy
    // must preserve newlines + blank-line delimiters or EventSource on
    // the browser side fails to parse.
    const eventCount = (wire.match(/^event: decisions$/gm) ?? []).length;
    expect(eventCount).toBe(2);

    // The first event must include the JSON payload AND the proxy-echoed
    // Authorization header — proves the token was injected upstream AND
    // the JSON survived the proxy hop.
    expect(wire).toContain('"action":"BET"');
    expect(wire).toContain(`Bearer ${EXPECTED_TOKEN}`);
    // Heartbeat comment line is preserved.
    expect(wire).toMatch(/^: connected \d+$/m);
  });

  test("EventSource via the dashboard reads frames through the proxy", async ({ page }) => {
    // Browser-side end-to-end: open a tiny harness page that constructs
    // an EventSource at the proxy URL and reports the first received event
    // back to Playwright. Proves the live browser code path works, not
    // just the buffered request-fixture path above.
    await page.goto("/"); // any same-origin page is fine; we only need the origin.
    const firstEvent = await page.evaluate<{ kind: string; data: string }>(
      () =>
        new Promise((resolve, reject) => {
          const es = new EventSource("/api/proxy/api/state/stream");
          const timer = setTimeout(() => {
            es.close();
            reject(new Error("timeout waiting for SSE event"));
          }, 8000);
          es.addEventListener("decisions", (evt) => {
            clearTimeout(timer);
            es.close();
            resolve({ kind: "decisions", data: (evt as MessageEvent).data });
          });
          es.addEventListener("error", () => {
            // Don't reject — EventSource auto-reconnects on transient close.
          });
        }),
    );
    expect(firstEvent.kind).toBe("decisions");
    const decoded = JSON.parse(firstEvent.data) as { action: string };
    expect(decoded.action).toBe("BET");
  });
});
