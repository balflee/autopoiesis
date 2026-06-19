/**
 * dashboard/lib/load_sandbox_state.server.ts — server-only loader.
 *
 * Companion to {@link import("./load_sandbox_state")} that owns the
 * `node:fs` reads against the sandbox state directory. Split out so
 * the client bundle is not polluted with Node built-ins (the main
 * file is marked `"use client"`).
 *
 * Path resolution:
 *   - Default root is `<repoRoot>/state/sandbox` resolved against
 *     `process.cwd()`. In production (Next.js `npm start` from the
 *     `dashboard/` directory) we expect cwd to be `dashboard/`, so the
 *     state dir is one level up. Tests inject `root` directly.
 *   - The env var `SANDBOX_STATE_DIR` overrides the resolved path —
 *     useful when the dashboard is hosted off-repo (e.g. a separate
 *     deploy reads a mounted volume).
 *
 * Failure modes:
 *   - Missing dir → `lag_alerts: [{ kind: "cold_boot" }]` + empty bundle
 *   - Missing snapshot but dir present → `missing_snapshot`
 *   - Snapshot older than 30 s → `snapshot_stale`
 *   - Any fs read raises → `fs_error` with a short detail; partial
 *     bundle still returned so the dashboard keeps painting.
 */

import { promises as fs } from "node:fs";
import path from "node:path";

// IMPORTANT: import the *shared* helpers (no `"use client"`) so this
// server module can invoke them as plain values. Importing from
// `load_sandbox_state.ts` (a client module) would emit the runtime
// error: "Attempted to call computeLagAlerts() from the server".
import {
  DEFAULT_TAIL_N,
  EMPTY_RAW_SANDBOX_FILES,
  foldSandboxBundle,
  type LagAlert,
  type RawSandboxFiles,
  type SandboxStateBundle,
} from "@/lib/sandbox_state_shared";

const DECISIONS_FILENAME = "decisions.jsonl";
const SETTLED_BETS_FILENAME = "settled_bets.jsonl";
const SNAPSHOT_FILENAME = "agent_state.json";
// Living Stage P1 — the divine economy streams.
const GODS_TREASURY_FILENAME = "gods_treasury.jsonl";
const DEATHS_FILENAME = "deaths.jsonl";

/** Resolve the sandbox state root — env override > default. */
export function resolveSandboxRoot(): string {
  const env = process.env.SANDBOX_STATE_DIR;
  if (env && env.length > 0) return env;
  return path.join(process.cwd(), "..", "state", "sandbox");
}

/** Options for the server loader. Tests inject `root` + `now`. */
export interface ServerLoaderOptions {
  readonly root?: string;
  readonly tailN?: number;
  readonly now?: () => number;
}

/**
 * Read the live sandbox state from disk and assemble a bundle.
 *
 * Defensive: every fs read is independently try/wrapped so a torn
 * write or a half-created directory does not take the route down. A
 * non-ENOENT read failure becomes an `fs_error` lag alert; ENOENT is
 * silent (the file just hasn't been written yet). The raw strings are
 * then handed to the single {@link foldSandboxBundle} so this path and
 * the over-the-backend path can never drift.
 */
export async function loadSandboxBundle(
  opts: ServerLoaderOptions = {},
): Promise<SandboxStateBundle> {
  const root = opts.root ?? resolveSandboxRoot();
  const tailN = opts.tailN ?? DEFAULT_TAIL_N;
  const now = opts.now ?? Date.now;

  let dirExists = true;
  try {
    await fs.stat(root);
  } catch {
    dirExists = false;
  }

  const errors: LagAlert[] = [];

  /**
   * Read one file's text. ENOENT → null (silent). Any other read error →
   * null + an `fs_error` alert so the dashboard surfaces the degradation
   * without crashing the poll.
   */
  const readFileOrNull = async (filename: string): Promise<string | null> => {
    try {
      return await fs.readFile(path.join(root, filename), "utf-8");
    } catch (err) {
      if (
        dirExists &&
        err instanceof Error &&
        err.message &&
        !/ENOENT/.test(err.message)
      ) {
        errors.push({
          kind: "fs_error",
          detail: `${filename} read failed: ${err.message}`,
          severity: "error",
        });
      }
      return null;
    }
  };

  const raw: RawSandboxFiles = {
    dirExists,
    snapshot: await readFileOrNull(SNAPSHOT_FILENAME),
    decisions: await readFileOrNull(DECISIONS_FILENAME),
    settled: await readFileOrNull(SETTLED_BETS_FILENAME),
    // Living Stage P1 — the divine economy streams.
    treasury: await readFileOrNull(GODS_TREASURY_FILENAME),
    deaths: await readFileOrNull(DEATHS_FILENAME),
  };

  // Behaviour parity with the pre-fold loader: a PRESENT but unparseable
  // `agent_state.json` (a torn mid-write) is an operator-visible error, so
  // it surfaces an `fs_error` alert IN ADDITION to the `missing_snapshot`
  // the fold emits when it can't parse the snapshot. (ENOENT — the file just
  // isn't written yet — already returned null above without an alert.) The
  // fold re-parses the same string; the duplicate parse of one small file is
  // negligible and keeps the fold the single source of the assembled bundle.
  if (raw.snapshot != null) {
    try {
      JSON.parse(raw.snapshot);
    } catch (err) {
      errors.push({
        kind: "fs_error",
        detail: `${SNAPSHOT_FILENAME} read failed: ${
          err instanceof Error ? err.message : String(err)
        }`,
        severity: "error",
      });
    }
  }

  return foldSandboxBundle(raw, now(), tailN, errors);
}

/* ------------------------------------------------------------------ */
/* Off-box data path — fetch raw state from the backend, then fold     */
/* ------------------------------------------------------------------ */

/** The backend envelope shape returned by `GET /api/sandbox/raw`. */
interface SandboxRawEnvelope {
  readonly dir_exists: boolean;
  readonly files: Readonly<Record<string, string | null>>;
}

/** Options for {@link loadSandboxBundleFromBackend} — tests inject these. */
export interface BackendLoaderOptions {
  readonly tailN?: number;
  readonly now?: () => number;
  /** Override `globalThis.fetch` — tests stub this. */
  readonly fetchImpl?: typeof fetch;
  /** Override `process.env.DASHBOARD_API_URL`. */
  readonly apiBase?: string;
  /** Override `process.env.DASHBOARD_API_TOKEN`. */
  readonly apiToken?: string;
}

/**
 * Fetch the raw sandbox state from the backend and fold it into a bundle.
 *
 * Used when the dashboard is deployed where the loop's state volume is
 * NOT mounted (Vercel `/living`, while the loop runs on Railway). The
 * server-side `/api/sandbox` route calls this instead of {@link
 * loadSandboxBundle} when `DASHBOARD_API_URL` is set. The bearer token is
 * read from `process.env` (server-only, never `NEXT_PUBLIC_*`) and stays
 * on the Vercel server — exactly like `dashboard/app/api/proxy`.
 *
 * Never throws: a missing URL/token, a non-2xx response, or a network
 * error all fold to a cold_boot bundle plus a descriptive `fs_error`
 * alert, so the page keeps painting + shows the degradation.
 */
export async function loadSandboxBundleFromBackend(
  opts: BackendLoaderOptions = {},
): Promise<SandboxStateBundle> {
  const tailN = opts.tailN ?? DEFAULT_TAIL_N;
  const now = opts.now ?? Date.now;
  const fetcher =
    opts.fetchImpl ?? (typeof fetch !== "undefined" ? fetch : null);
  const apiBase = opts.apiBase ?? process.env.DASHBOARD_API_URL;
  const apiToken = opts.apiToken ?? process.env.DASHBOARD_API_TOKEN;

  const degraded = (detail: string): SandboxStateBundle =>
    foldSandboxBundle(EMPTY_RAW_SANDBOX_FILES, now(), tailN, [
      { kind: "fs_error", detail, severity: "error" },
    ]);

  if (fetcher == null) return degraded("fetch is unavailable on the server");
  if (!apiBase || apiBase.length === 0) {
    return degraded("DASHBOARD_API_URL is not set on the server");
  }
  if (!apiToken || apiToken.length === 0) {
    return degraded("DASHBOARD_API_TOKEN is not set on the server");
  }

  const target = `${apiBase.replace(/\/+$/, "")}/api/sandbox/raw`;
  let res: Response;
  try {
    res = await fetcher(target, {
      headers: { Authorization: `Bearer ${apiToken}` },
      cache: "no-store",
    });
  } catch (err) {
    return degraded(
      `backend /api/sandbox/raw fetch failed: ${
        err instanceof Error ? err.message : String(err)
      }`,
    );
  }
  if (!res.ok) {
    return degraded(`backend /api/sandbox/raw → HTTP ${res.status}`);
  }

  let envelope: SandboxRawEnvelope;
  try {
    envelope = (await res.json()) as SandboxRawEnvelope;
  } catch (err) {
    return degraded(
      `backend /api/sandbox/raw returned non-JSON: ${
        err instanceof Error ? err.message : String(err)
      }`,
    );
  }

  const files = envelope.files ?? {};
  const raw: RawSandboxFiles = {
    dirExists: envelope.dir_exists === true,
    snapshot: files[SNAPSHOT_FILENAME] ?? null,
    decisions: files[DECISIONS_FILENAME] ?? null,
    settled: files[SETTLED_BETS_FILENAME] ?? null,
    treasury: files[GODS_TREASURY_FILENAME] ?? null,
    deaths: files[DEATHS_FILENAME] ?? null,
  };
  return foldSandboxBundle(raw, now(), tailN);
}
