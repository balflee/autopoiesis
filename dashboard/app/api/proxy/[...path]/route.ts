/**
 * dashboard/app/api/proxy/[...path]/route.ts — same-origin Next.js server
 * route that injects the bearer token server-side so the browser bundle
 * never carries it.
 *
 * T-D-011 sprint_10 — root cause of the prod HTTP 401 on `/workshop`:
 *
 *   - The browser bundle previously read `NEXT_PUBLIC_DASHBOARD_API_TOKEN`
 *     and called `https://<backend>` directly. Hard rule: anything prefixed
 *     `NEXT_PUBLIC_` is shipped to every visitor's browser. So the only way
 *     to keep the token secret on Vercel is to NEVER expose it client-side.
 *   - The fix is this proxy. Browser calls relative `/api/proxy/<path>`;
 *     this handler (executing in Vercel's server runtime) attaches the
 *     `Authorization: Bearer ${DASHBOARD_API_TOKEN}` header and forwards to
 *     `${DASHBOARD_API_URL}/<path>`. The token never crosses the wire to
 *     the browser.
 *
 * Constraints enforced here:
 *
 *   1. `DASHBOARD_API_URL` and `DASHBOARD_API_TOKEN` MUST be read from
 *      `process.env.*` (server-only) — neither is `NEXT_PUBLIC_*`.
 *   2. Missing `DASHBOARD_API_URL` → 503 with a clear error so a
 *      misconfigured deploy surfaces fast.
 *   3. Missing `DASHBOARD_API_TOKEN` → 401 (per T-D-011 spec: "401 when
 *      server token missing"). This mirrors the backend's own auth gate
 *      so the dashboard reacts identically whether the failure is
 *      upstream-auth or proxy-misconfigured.
 *   4. `export const dynamic = "force-dynamic"` — keeps Next.js from
 *      caching the response between polls; required for SSE streams + for
 *      our request-time env reads to be honoured.
 *   5. `runtime = "nodejs"` — we forward `request.body` as a Web stream
 *      so SSE pass-through works. Edge runtime would also work but the
 *      rest of the dashboard server code is on Node, so we stay consistent.
 *   6. Query string + body are forwarded verbatim. Response status + body
 *      + headers (minus hop-by-hop) are passed through. `Content-Type:
 *      text/event-stream` is preserved so EventSource on the browser side
 *      sees the SSE wire format unchanged.
 *
 * Test seam (gated by `PROXY_TEST_MODE === "1"` env var so it can NEVER
 * fire in production deploys):
 *
 *   - Request header `x-genesis-proxy-test-clear-token: 1` → the proxy
 *     behaves as if `DASHBOARD_API_TOKEN` were missing and short-circuits
 *     with 401. Used by `tests/dashboard/proxy/test_proxy_401_missing_token`.
 *     The guard prevents this header from being honoured on Vercel
 *     production (where `PROXY_TEST_MODE` is unset) — a hostile request
 *     setting this header on prod would be ignored.
 */

import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
// Vercel function timeout — SSE streams need the full duration to keep
// the connection open. force-dynamic alone keeps Next.js from caching;
// maxDuration extends the function's allowance (no-op on hobby plans
// where the cap is 10s, but documents intent + works on Pro/Enterprise).
export const maxDuration = 300;

/**
 * Hop-by-hop headers per RFC 7230 §6.1 — never pass these through. Plus
 * `content-encoding` and `content-length` because `fetch` may have already
 * decoded the body OR the upstream length doesn't match the proxied length
 * (Node's `undici` does transparent gzip decoding).
 */
const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailers",
  "transfer-encoding",
  "upgrade",
  "content-encoding",
  "content-length",
]);

type RouteContext = { params: Promise<{ path: string[] }> };

async function proxyRequest(
  request: NextRequest,
  context: RouteContext,
): Promise<Response> {
  const { path } = await context.params;
  const apiBase = process.env.DASHBOARD_API_URL;
  const apiToken = process.env.DASHBOARD_API_TOKEN;
  const testMode = process.env.PROXY_TEST_MODE === "1";

  if (!apiBase || apiBase.length === 0) {
    return NextResponse.json(
      {
        detail:
          "proxy misconfigured: DASHBOARD_API_URL is not set on the server",
      },
      { status: 503 },
    );
  }

  // Test seam — only honoured when PROXY_TEST_MODE=1. Lets the Playwright
  // 401-missing-token spec exercise the missing-token branch without
  // restarting the next-start process.
  const testClearToken =
    testMode &&
    request.headers.get("x-genesis-proxy-test-clear-token") === "1";

  if (!apiToken || apiToken.length === 0 || testClearToken) {
    return NextResponse.json(
      {
        detail:
          "proxy misconfigured: DASHBOARD_API_TOKEN is not set on the server",
      },
      { status: 401 },
    );
  }

  // Reconstruct the upstream URL: base + "/" + joined segments + query string.
  const segments = (path ?? []).map(encodeURIComponent).join("/");
  const url = new URL(request.url);
  const target = `${apiBase.replace(/\/+$/, "")}/${segments}${url.search}`;

  // Build forwarded headers. We strip every Authorization the browser might
  // send (the proxy is the SOLE source of truth) and inject the server-side
  // token. We pass through Accept + Content-Type + Cache-Control so SSE
  // and JSON content negotiation work upstream.
  const fwdHeaders = new Headers();
  fwdHeaders.set("Authorization", `Bearer ${apiToken}`);
  const passThroughReqHeaders = [
    "accept",
    "accept-language",
    "content-type",
    "cache-control",
    "user-agent",
  ];
  for (const name of passThroughReqHeaders) {
    const value = request.headers.get(name);
    if (value !== null) fwdHeaders.set(name, value);
  }

  // Body forwarding — only methods with bodies. We read into a Buffer
  // because Node's fetch requires either no body OR a body with a known
  // length; streaming Web bodies through fetch in Node still needs
  // `duplex: "half"` which is unstable across Node versions. Buffer is
  // safe for the small JSON payloads this proxy carries.
  let body: BodyInit | undefined;
  if (request.method !== "GET" && request.method !== "HEAD") {
    const raw = await request.arrayBuffer();
    body = raw.byteLength > 0 ? raw : undefined;
  }

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers: fwdHeaders,
      body,
      // We don't want Next.js (or undici) caching anything — the upstream
      // is a stateful FastAPI control plane.
      cache: "no-store",
      redirect: "manual",
    });
  } catch (cause) {
    return NextResponse.json(
      {
        detail: `proxy upstream fetch failed: ${
          (cause as Error).message ?? String(cause)
        }`,
      },
      { status: 502 },
    );
  }

  // Forward upstream headers, minus hop-by-hop. Preserve Content-Type so
  // text/event-stream is honoured by EventSource on the browser side.
  const respHeaders = new Headers();
  upstream.headers.forEach((value, key) => {
    if (!HOP_BY_HOP.has(key.toLowerCase())) {
      respHeaders.set(key, value);
    }
  });
  // Belt-and-braces: explicitly disable downstream caching.
  respHeaders.set("Cache-Control", "no-store");

  // Pass through the body as a stream so SSE works. `upstream.body` is a
  // ReadableStream that ends when the upstream connection closes; the
  // browser EventSource will see the same wire bytes the upstream sent.
  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: respHeaders,
  });
}

export async function GET(
  request: NextRequest,
  context: RouteContext,
): Promise<Response> {
  return proxyRequest(request, context);
}

export async function POST(
  request: NextRequest,
  context: RouteContext,
): Promise<Response> {
  return proxyRequest(request, context);
}

export async function PUT(
  request: NextRequest,
  context: RouteContext,
): Promise<Response> {
  return proxyRequest(request, context);
}

export async function DELETE(
  request: NextRequest,
  context: RouteContext,
): Promise<Response> {
  return proxyRequest(request, context);
}

export async function PATCH(
  request: NextRequest,
  context: RouteContext,
): Promise<Response> {
  return proxyRequest(request, context);
}
