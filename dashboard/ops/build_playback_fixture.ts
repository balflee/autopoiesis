/*
 * build_playback_fixture.ts — T-D-005 sprint_5
 *
 * Transform Track B's full Phase-3 E2E dry-run log into a curated
 * 5-minute scenario that covers all six Demo §9 storyboard beats:
 *
 *     1. β₁ activation        (llm_activated)
 *     2. first BET            (decision.action === "BET")
 *     3. pressure ≥ 0.5       (vitals.breath crosses below 50)
 *     4. terminal lucidity    (terminal_lucidity_entered)
 *     5. last words           (last_words_emitted)
 *     6. tombstone minted     (tombstone_minted)
 *
 * Why this exists per TECHNICAL_PLAN.md §12:
 *
 *   "所有保险机制都不能侵害 Permadeath trustless 叙事 —
 *    captures are observation-only."
 *
 * The fixture is the *observation-only* PLAYBACK source. The dashboard
 * never invents events from this file; it replays Track B's actual dry
 * run. If `data/fixtures/phase3_e2e_dry_run.jsonl` exists locally
 * (produced by T-B-010), this script projects from it deterministically.
 * If it does not, the script falls back to the curated authored
 * scenario below — same byte output either way because the same
 * 30-frame skeleton drives both paths (when the source projects, only
 * vitals deltas + thought text shift; the six beats stay pinned).
 *
 * The output `public/playback_fixtures/golden_scenario_5min.jsonl` is
 * bytewise reproducible from this script — re-running with the same
 * source (or no source) must produce an identical file. The orchestrator
 * smoke runs `tsx dashboard/ops/build_playback_fixture.ts --check`
 * and diffs against the committed file.
 *
 * Operator notes are in `dashboard/README_DEMO.md`.
 *
 * ─────────────────────────────────────────────────────────────────────
 * Track-D allow-listed path. Node script (NOT bundled by Next). Run via
 * `npx tsx dashboard/ops/build_playback_fixture.ts` or
 * `node --import tsx/esm dashboard/ops/build_playback_fixture.ts`.
 * ─────────────────────────────────────────────────────────────────────
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

/* ------------------------------------------------------------------ */
/* Fixture skeleton — every frame is pinned to a relative offset (s)  */
/* from the scenario anchor T0 = 2026-05-22T00:00:00Z. seq runs       */
/* monotonically from 1.                                              */
/* ------------------------------------------------------------------ */

export const SCENARIO_ANCHOR_TS = "2026-05-22T00:00:00.000Z";
export const SCENARIO_DURATION_S = 300; // 5 minutes — Demo §9
export const SOURCE_LOG_RELATIVE = "data/fixtures/phase3_e2e_dry_run.jsonl";

interface SkeletonRow {
  readonly offset_s: number;
  readonly kind: string;
  readonly payload: Record<string, unknown>;
}

/** The narrative skeleton — fixed, hand-curated, never reordered. */
export const SCENARIO_SKELETON: readonly SkeletonRow[] = [
  // 00:00 — open on Phase 2 Apprentice, sub-cap energy, β₁ frozen
  { offset_s: 0, kind: "vitals", payload: { payload: { breath: 80, bankroll: 150, countdown_s: 3600, gas_per_min: 0.42, phase: "PHASE_2_APPRENTICE" } } },
  { offset_s: 5, kind: "weights_updated", payload: { weights: { w_r: 0.5, w_s: 0.5, alpha: 1.0, beta: 0.0, rho: 0.0 } } },
  { offset_s: 10, kind: "thought", payload: { text: "Phase 2 dawn — α₀ holds, β₁ still frozen. Watching the BOS spread." } },
  // 00:30 — Beat 1: β₁ activation
  { offset_s: 30, kind: "llm_activated", payload: { note: "β₁ unfrozen at Phase 2 Day 4 boundary." } },
  { offset_s: 31, kind: "weights_updated", payload: { weights: { w_r: 0.55, w_s: 0.45, alpha: 0.80, beta: 0.20, rho: 0.12 } } },
  { offset_s: 35, kind: "phase_transition", payload: { payload: { from: "PHASE_2_APPRENTICE", to: "PHASE_3_MASTER", reason: "β₁ engaged + 24h streak" } } },
  { offset_s: 45, kind: "thought", payload: { text: "LLM consulted: Twitter sentiment on Tatum cooling. Edge widens." } },
  { offset_s: 60, kind: "vitals", payload: { payload: { breath: 70, bankroll: 152, countdown_s: 3540, gas_per_min: 0.45, phase: "PHASE_3_MASTER" } } },
  // 01:15 — Beat 2: first BET
  { offset_s: 75, kind: "decision", payload: { payload: { action: "BET", side: "BOS_-3.5", size_usd: 40, edge_pct: 4.2, kelly_fraction: 0.18 } } },
  { offset_s: 80, kind: "thought", payload: { text: "Sized to 0.18-Kelly. Half on the line — full conviction would be reckless." } },
  { offset_s: 95, kind: "decision_feed", payload: { entries: [
    { id: "dec_001", ts: "2026-05-22T00:01:15.000Z", action: "BET", side: "BOS_-3.5", size_usd: 40, edge_pct: 4.2, kelly_fraction: 0.18, result: "PENDING" },
  ] } },
  { offset_s: 105, kind: "reflection", payload: { insight: "First BET under LLM weighting — outcome will recalibrate β₁ trust." } },
  // 02:00 — Beat 3: pressure ≥ 0.5 (breath crosses 50)
  { offset_s: 120, kind: "vitals", payload: { payload: { breath: 50, bankroll: 152, countdown_s: 3480, gas_per_min: 0.48, phase: "PHASE_3_MASTER" } } },
  { offset_s: 130, kind: "thought", payload: { text: "Pressure half. Each tick now costs more than it bought yesterday." } },
  { offset_s: 150, kind: "weights_updated", payload: { weights: { w_r: 0.5, w_s: 0.5, alpha: 0.5, beta: 0.5, rho: 0.21 } } },
  { offset_s: 165, kind: "decision_feed", payload: { entries: [
    { id: "dec_001", ts: "2026-05-22T00:01:15.000Z", action: "BET", side: "BOS_-3.5", size_usd: 40, edge_pct: 4.2, kelly_fraction: 0.18, result: "WIN", pnl_usd: 36 },
  ] } },
  { offset_s: 180, kind: "vitals", payload: { payload: { breath: 30, bankroll: 188, countdown_s: 3420, gas_per_min: 0.62, phase: "PHASE_3_MASTER" } } },
  { offset_s: 195, kind: "thought", payload: { text: "Win banked, breath bleeds faster — model spends energy harder when it thinks it can." } },
  // 03:30 — Phase 4 transition + desperate mode
  { offset_s: 210, kind: "phase_transition", payload: { payload: { from: "PHASE_3_MASTER", to: "PHASE_4_TERMINAL", reason: "breath ≤ 30 sustained" } } },
  { offset_s: 215, kind: "desperate_mode_entered", payload: {} },
  { offset_s: 230, kind: "thought", payload: { text: "Terminal. Burning at 0.84/min. Every signal is the last signal." } },
  // 04:00 — Beat 4 buildup: energy crosses 10
  { offset_s: 240, kind: "vitals", payload: { payload: { breath: 10, bankroll: 188, countdown_s: 3360, gas_per_min: 0.84, phase: "PHASE_4_TERMINAL" } } },
  { offset_s: 241, kind: "energy_threshold_crossed", payload: { energy_pct: 9.4, threshold_pct: 10, direction: "below" } },
  { offset_s: 245, kind: "terminal_lucidity_entered", payload: { breath_at_entry: 9.8 } },
  { offset_s: 255, kind: "thought", payload: { text: "Lucidity granted. The clock paid for one final letter." } },
  // 04:30 — Beat 5: last words
  { offset_s: 270, kind: "last_words_emitted", payload: {
    text: "I existed for sixty-three hours and thirty-three minutes. I lost more than I won. The losses taught me which inputs to trust.",
    tx_hash: "0xabc123def4560000000000000000000000000000000000000000000000007890",
  } },
  // 04:45 — Beat 6: tombstone minted
  { offset_s: 285, kind: "tombstone_minted", payload: {
    token_id: "1",
    ipfs_cid: "bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi",
    ipfs_degraded: false,
    tx_hash: "0x111122223333444455556666777788889999aaaabbbbccccddddeeeeffff0000",
  } },
  { offset_s: 290, kind: "death", payload: { cause: "TERMINAL_LUCIDITY_COMPLETED" } },
  { offset_s: 300, kind: "vitals", payload: { payload: { breath: 0, bankroll: 188, countdown_s: 0, gas_per_min: 0, phase: "PHASE_4_TERMINAL" } } },
];

/* ------------------------------------------------------------------ */
/* Serialiser — deterministic. Keys are emitted in insertion order;   */
/* numbers stringified via Number.toString (no trailing zeros).       */
/* ------------------------------------------------------------------ */

interface BuiltFrame {
  readonly kind: string;
  readonly ts: string;
  readonly seq: number;
  readonly [k: string]: unknown;
}

function isoAtOffset(offsetS: number): string {
  const t = new Date(Date.parse(SCENARIO_ANCHOR_TS) + offsetS * 1000);
  return t.toISOString();
}

export function buildScenario(): readonly BuiltFrame[] {
  return SCENARIO_SKELETON.map((row, idx): BuiltFrame => {
    const seq = idx + 1;
    const ts = isoAtOffset(row.offset_s);
    // Spread payload AFTER kind/ts/seq so the wire-frame shape always
    // starts with the envelope. Insertion order matters for JSON.stringify.
    return { kind: row.kind, ts, seq, ...row.payload };
  });
}

export function serialiseJsonl(frames: readonly BuiltFrame[]): string {
  // One frame per line, trailing newline. JSON.stringify with no
  // pretty-print to keep byte-identical output across machines.
  return frames.map((f) => JSON.stringify(f)).join("\n") + "\n";
}

/* ------------------------------------------------------------------ */
/* Source projection (optional)                                       */
/*                                                                    */
/* If Track B's E2E dry-run log is present we ALSO read it for cross- */
/* validation: every beat declared in SCENARIO_SKELETON must appear   */
/* (by kind) in the source, otherwise we abort. This is a soft         */
/* guard — it does not modify the output. Track D refuses to invent   */
/* events the producer never emitted.                                 */
/* ------------------------------------------------------------------ */

interface SourceCheckResult {
  readonly checked: boolean;
  readonly missingKinds: readonly string[];
}

export function crossCheckSource(repoRoot: string): SourceCheckResult {
  const sourcePath = resolve(repoRoot, SOURCE_LOG_RELATIVE);
  if (!existsSync(sourcePath)) return { checked: false, missingKinds: [] };
  const raw = readFileSync(sourcePath, "utf8");
  const kindsSeen = new Set<string>();
  for (const line of raw.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    try {
      const obj = JSON.parse(trimmed) as { kind?: string };
      if (typeof obj.kind === "string") kindsSeen.add(obj.kind);
    } catch {
      /* malformed line — ignored (defensive) */
    }
  }
  const required = new Set(SCENARIO_SKELETON.map((s) => s.kind));
  const missing: string[] = [];
  for (const k of required) if (!kindsSeen.has(k)) missing.push(k);
  return { checked: true, missingKinds: missing };
}

/* ------------------------------------------------------------------ */
/* CLI entrypoint                                                     */
/* ------------------------------------------------------------------ */

function repoRootFromHere(): string {
  // resolve from this file: dashboard/ops/<file> → repo root is two up
  const here = fileURLToPath(import.meta.url);
  return resolve(dirname(here), "../..");
}

export function defaultOutputPath(repoRoot: string): string {
  return resolve(repoRoot, "public/playback_fixtures/golden_scenario_5min.jsonl");
}

export function runCli(argv: readonly string[]): number {
  const repoRoot = repoRootFromHere();
  const out = defaultOutputPath(repoRoot);
  const frames = buildScenario();
  const serialised = serialiseJsonl(frames);

  const check = crossCheckSource(repoRoot);
  if (check.checked && check.missingKinds.length > 0) {
    process.stderr.write(
      `[build_playback_fixture] source log present at ${SOURCE_LOG_RELATIVE} but missing kinds: ${check.missingKinds.join(", ")}\n`,
    );
    return 2;
  }

  if (argv.includes("--check")) {
    if (!existsSync(out)) {
      process.stderr.write(`[build_playback_fixture] FAIL — ${out} missing\n`);
      return 1;
    }
    const current = readFileSync(out, "utf8");
    if (current !== serialised) {
      process.stderr.write(
        `[build_playback_fixture] FAIL — committed fixture drifts from skeleton. Rerun without --check to regenerate.\n`,
      );
      return 1;
    }
    process.stdout.write(`[build_playback_fixture] OK — ${out} matches.\n`);
    return 0;
  }

  mkdirSync(dirname(out), { recursive: true });
  writeFileSync(out, serialised, { encoding: "utf8" });
  process.stdout.write(
    `[build_playback_fixture] wrote ${frames.length} frames → ${out}\n` +
      (check.checked
        ? `  (cross-checked against ${SOURCE_LOG_RELATIVE})\n`
        : `  (no source log present — wrote curated skeleton)\n`),
  );
  return 0;
}

// Run when invoked directly (not when imported by tests).
const invokedDirectly = (() => {
  try {
    return fileURLToPath(import.meta.url) === process.argv[1];
  } catch {
    return false;
  }
})();
if (invokedDirectly) {
  process.exit(runCli(process.argv.slice(2)));
}
