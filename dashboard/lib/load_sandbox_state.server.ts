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
  computeLagAlerts,
  DEFAULT_TAIL_N,
  lastN,
  parseJsonl,
  type AgentStateSnapshotData,
  type DecisionRecordData,
  type LagAlert,
  type SandboxStateBundle,
  type SettledBetRecordData,
} from "@/lib/sandbox_state_shared";

const DECISIONS_FILENAME = "decisions.jsonl";
const SETTLED_BETS_FILENAME = "settled_bets.jsonl";
const SNAPSHOT_FILENAME = "agent_state.json";

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
 * write or a half-created directory does not take the route down.
 * The returned `lag_alerts` describes any degradation.
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

  const snapshotPath = path.join(root, SNAPSHOT_FILENAME);
  let snapshot: AgentStateSnapshotData | null = null;
  const errors: LagAlert[] = [];
  try {
    const raw = await fs.readFile(snapshotPath, "utf-8");
    snapshot = JSON.parse(raw) as AgentStateSnapshotData;
  } catch (err) {
    if (dirExists && err instanceof Error && err.message && !/ENOENT/.test(err.message)) {
      errors.push({
        kind: "fs_error",
        detail: `agent_state.json read failed: ${err.message}`,
        severity: "error",
      });
    }
    snapshot = null;
  }

  const decisionsPath = path.join(root, DECISIONS_FILENAME);
  let decisions: DecisionRecordData[] = [];
  try {
    const raw = await fs.readFile(decisionsPath, "utf-8");
    decisions = lastN(parseJsonl<DecisionRecordData>(raw), tailN);
  } catch (err) {
    if (dirExists && err instanceof Error && err.message && !/ENOENT/.test(err.message)) {
      errors.push({
        kind: "fs_error",
        detail: `decisions.jsonl read failed: ${err.message}`,
        severity: "error",
      });
    }
    decisions = [];
  }

  const settledPath = path.join(root, SETTLED_BETS_FILENAME);
  let settled: SettledBetRecordData[] = [];
  try {
    const raw = await fs.readFile(settledPath, "utf-8");
    settled = lastN(parseJsonl<SettledBetRecordData>(raw), tailN);
  } catch (err) {
    if (dirExists && err instanceof Error && err.message && !/ENOENT/.test(err.message)) {
      errors.push({
        kind: "fs_error",
        detail: `settled_bets.jsonl read failed: ${err.message}`,
        severity: "error",
      });
    }
    settled = [];
  }

  const lag_alerts = [...computeLagAlerts(snapshot, now(), dirExists), ...errors];

  return {
    snapshot,
    recent_decisions: decisions,
    recent_settled: settled,
    lag_alerts,
    served_ts: new Date(now()).toISOString(),
    is_mock: false,
  };
}
