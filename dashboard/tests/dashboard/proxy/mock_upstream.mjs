#!/usr/bin/env node
/**
 * mock_upstream.mjs — tiny HTTP server that stands in for the Track B
 * FastAPI control plane while the T-D-011 proxy specs run.
 *
 * Boot under Playwright's `webServer` so the lifecycle matches `next start`.
 * Listens on `PROXY_MOCK_PORT` (default 8765). The Next.js webServer is
 * configured with `DASHBOARD_API_URL=http://127.0.0.1:8765` so every proxy
 * request forwards here.
 *
 * Endpoints (proof-of-concept — tests only assert these):
 *
 *   GET  /healthz             → 200 { ok: true, ts, auth }
 *   GET  /api/agent/status    → 200 { phase, breath, ..., auth }
 *   POST /api/backtest/run    → 202 { run_id, status, echoed_body, auth }
 *   GET  /api/state/stream    → SSE — emits two decision events then closes
 *   GET  /__expect_401        → 401 { detail: "unauthorized" }
 *   anything else             → 404 { detail: "not found" }
 *
 * Every response echoes the Authorization header so the proxy specs can
 * assert that the server-side token was injected verbatim.
 */

import { createServer } from "node:http";

const PORT = Number(process.env.PROXY_MOCK_PORT ?? "8765");
const EXPECTED_TOKEN = process.env.MOCK_UPSTREAM_EXPECTED_TOKEN ?? null;

function json(res, status, body) {
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    "Content-Type": "application/json",
    "Content-Length": Buffer.byteLength(payload),
    "Cache-Control": "no-store",
  });
  res.end(payload);
}

async function readBody(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  if (chunks.length === 0) return null;
  const raw = Buffer.concat(chunks).toString("utf8");
  try {
    return JSON.parse(raw);
  } catch {
    return raw;
  }
}

function checkAuth(req) {
  const auth = req.headers.authorization ?? null;
  // EXPECTED_TOKEN is an optional sanity gate — set in playwright config to
  // assert the proxy is using the exact token the test env declared.
  if (EXPECTED_TOKEN && auth !== `Bearer ${EXPECTED_TOKEN}`) {
    return { ok: false, auth };
  }
  return { ok: true, auth };
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url ?? "/", `http://127.0.0.1:${PORT}`);
  const auth = checkAuth(req);

  // /healthz — used by the production smoke (curl /api/proxy/healthz).
  if (req.method === "GET" && url.pathname === "/healthz") {
    return json(res, 200, {
      ok: true,
      ts: new Date().toISOString(),
      auth: auth.auth,
    });
  }

  // /api/agent/status — happy GET pass-through.
  if (req.method === "GET" && url.pathname === "/api/agent/status") {
    if (!auth.ok) return json(res, 401, { detail: "bad token" });
    return json(res, 200, {
      phase: "PHASE_1_INFANCY",
      breath: 100.0,
      last_tick_ts: "2026-05-28T00:00:00Z",
      current_weights: null,
      llm_cost_usd_this_month: 0.0,
      pending_proposals_count: 0,
      running: false,
      run_id: null,
      _proxy_echo: { auth: auth.auth, query: url.search },
    });
  }

  // /api/backtest/run — happy POST pass-through.
  if (req.method === "POST" && url.pathname === "/api/backtest/run") {
    if (!auth.ok) return json(res, 401, { detail: "bad token" });
    const body = await readBody(req);
    return json(res, 202, {
      run_id: "test_run_id_001",
      status: "accepted",
      _proxy_echo: { auth: auth.auth, body },
    });
  }

  // /api/state/stream — SSE pass-through smoke. Emits two events and ends.
  if (req.method === "GET" && url.pathname === "/api/state/stream") {
    if (!auth.ok) {
      res.writeHead(401, { "Content-Type": "application/json" });
      return res.end(JSON.stringify({ detail: "bad token" }));
    }
    res.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-store",
      Connection: "keep-alive",
      // No Content-Length — SSE is open-ended.
    });
    // Initial heartbeat so EventSource fires `open` immediately.
    res.write(`: connected ${Date.now()}\n\n`);
    res.write(
      `event: decisions\ndata: ${JSON.stringify({
        action: "BET",
        side: "PLAYER_A",
        size_usd: 5.0,
        edge_pct: 4.2,
        kelly_fraction: 0.025,
        ts: new Date().toISOString(),
        _proxy_echo_auth: auth.auth,
      })}\n\n`,
    );
    res.write(
      `event: decisions\ndata: ${JSON.stringify({
        action: "NO_BET",
        ts: new Date().toISOString(),
      })}\n\n`,
    );
    // Hold the connection open briefly so the EventSource client has time
    // to consume both events, then close. setTimeout keeps the event loop
    // alive without polling.
    setTimeout(() => res.end(), 250);
    return;
  }

  // /__expect_401 — explicit upstream 401, used to test pass-through.
  if (req.method === "GET" && url.pathname === "/__expect_401") {
    return json(res, 401, { detail: "unauthorized" });
  }

  return json(res, 404, { detail: `mock upstream: no handler for ${req.method} ${url.pathname}` });
});

server.listen(PORT, "127.0.0.1", () => {
  // Use stderr — the playwright webServer pipes stderr but ignores stdout
  // by default in our config.
  process.stderr.write(`[mock_upstream] listening on http://127.0.0.1:${PORT}\n`);
});

// Graceful shutdown so Playwright's webServer teardown is clean.
for (const sig of ["SIGINT", "SIGTERM"]) {
  process.on(sig, () => {
    server.close(() => process.exit(0));
  });
}
