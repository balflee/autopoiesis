/**
 * load_sandbox_backend.test.ts — the off-box (Vercel `/living`) data path.
 *
 * `loadSandboxBundleFromBackend` fetches `GET /api/sandbox/raw` from the
 * backend (token injected server-side) and folds the raw strings. These
 * tests pin:
 *
 *   1. happy path  — envelope → folded bundle (snapshot + treasury)
 *   2. bearer token is attached to the upstream request
 *   3. missing DASHBOARD_API_URL → cold_boot + fs_error, never throws
 *   4. missing DASHBOARD_API_TOKEN → fs_error, never throws
 *   5. non-2xx upstream → fs_error with the status code
 *   6. network throw → fs_error, never propagates
 */

import { describe, expect, it, vi } from "vitest";

import { loadSandboxBundleFromBackend } from "@/lib/load_sandbox_state.server";

const FROZEN_NOW = Date.parse("2026-06-19T12:01:00Z");

const SNAPSHOT_JSON = JSON.stringify({
  snapshot_ts: "2026-06-19T12:00:55Z",
  phase: "PHASE_2_APPRENTICE",
  breath: 88,
  bankroll_usd: 102.5,
  last_tick: 1,
  weights: null,
  incarnation_number: 4,
});

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return {
    ok,
    status,
    json: async () => body,
  } as unknown as Response;
}

describe("loadSandboxBundleFromBackend", () => {
  it("folds the backend envelope into a bundle", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse({
        dir_exists: true,
        files: {
          "agent_state.json": SNAPSHOT_JSON,
          "gods_treasury.jsonl":
            JSON.stringify({ type: "tribute", success: true, amount_usd: 500 }) +
            "\n",
          "decisions.jsonl": null,
          "settled_bets.jsonl": null,
          "deaths.jsonl": null,
        },
      }),
    );
    const bundle = await loadSandboxBundleFromBackend({
      fetchImpl: fetchImpl as unknown as typeof fetch,
      apiBase: "https://backend.example",
      apiToken: "tok",
      now: () => FROZEN_NOW,
    });
    expect(bundle.snapshot?.breath).toBe(88);
    expect(bundle.incarnation_number).toBe(4);
    expect(bundle.gods_revenue_cumulative_usd).toBe(500);
    expect(bundle.lag_alerts).toEqual([]);
  });

  it("attaches the bearer token and hits /api/sandbox/raw", async () => {
    const fetchImpl = vi.fn(
      async (_url: string, _init?: RequestInit): Promise<Response> =>
        jsonResponse({ dir_exists: false, files: {} }),
    );
    await loadSandboxBundleFromBackend({
      fetchImpl: fetchImpl as unknown as typeof fetch,
      apiBase: "https://backend.example/",
      apiToken: "secret-token",
      now: () => FROZEN_NOW,
    });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    const [url, init] = fetchImpl.mock.calls[0]!;
    expect(url).toBe("https://backend.example/api/sandbox/raw");
    const headers = (init?.headers ?? {}) as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer secret-token");
  });

  it("degrades (cold_boot + fs_error) when DASHBOARD_API_URL is missing", async () => {
    const fetchImpl = vi.fn();
    const bundle = await loadSandboxBundleFromBackend({
      fetchImpl: fetchImpl as unknown as typeof fetch,
      apiBase: "",
      apiToken: "tok",
      now: () => FROZEN_NOW,
    });
    expect(fetchImpl).not.toHaveBeenCalled();
    const kinds = bundle.lag_alerts.map((a) => a.kind);
    expect(kinds).toContain("cold_boot");
    expect(kinds).toContain("fs_error");
    expect(bundle.snapshot).toBeNull();
  });

  it("degrades when DASHBOARD_API_TOKEN is missing", async () => {
    const fetchImpl = vi.fn();
    const bundle = await loadSandboxBundleFromBackend({
      fetchImpl: fetchImpl as unknown as typeof fetch,
      apiBase: "https://backend.example",
      apiToken: "",
      now: () => FROZEN_NOW,
    });
    expect(fetchImpl).not.toHaveBeenCalled();
    expect(bundle.lag_alerts.map((a) => a.kind)).toContain("fs_error");
  });

  it("degrades with the status code on a non-2xx upstream", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse({}, false, 401));
    const bundle = await loadSandboxBundleFromBackend({
      fetchImpl: fetchImpl as unknown as typeof fetch,
      apiBase: "https://backend.example",
      apiToken: "tok",
      now: () => FROZEN_NOW,
    });
    const fsErr = bundle.lag_alerts.find((a) => a.kind === "fs_error");
    expect(fsErr?.detail).toContain("401");
  });

  it("never throws on a network error", async () => {
    const fetchImpl = vi.fn(async () => {
      throw new Error("ECONNREFUSED");
    });
    const bundle = await loadSandboxBundleFromBackend({
      fetchImpl: fetchImpl as unknown as typeof fetch,
      apiBase: "https://backend.example",
      apiToken: "tok",
      now: () => FROZEN_NOW,
    });
    const fsErr = bundle.lag_alerts.find((a) => a.kind === "fs_error");
    expect(fsErr?.detail).toContain("ECONNREFUSED");
  });
});
