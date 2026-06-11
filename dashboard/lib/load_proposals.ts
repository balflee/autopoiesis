/**
 * load_proposals.ts — types + parsers for the L3 strategy-proposal stream.
 *
 * Source of truth (CONSUMER side, this side):
 *   `.dev/contracts/strategy_proposal_schema.v0.1.0.json`
 *
 * T-B-025 published the schema and recorded `consumers: ["D"]` in the
 * registry. T-D-010 registers the dashboard as that consumer (see
 * `interface_diff.json` in this delivery). The schema is designed to stay
 * v0.1.0 across the sprint_9 → sprint_10 advisor swap; we mirror only the
 * required + documented optional fields here.
 *
 * The dashboard reads proposals in TWO modes:
 *
 *   1. STREAM (live, sprint_9):
 *      Track B's `/api/state/stream` SSE emits one `event: proposals` per
 *      appended JSONL line — see `lib/sse_subscribe.ts`.
 *
 *   2. BATCH (replay, sprint_10):
 *      The dashboard will fetch the full `proposals.jsonl` snapshot for
 *      replay. The {@link parseProposalsJsonl} parser handles that shape.
 *
 * For sprint_9 the dashboard's empty-state copy is canonical: ProposalReview
 * renders "No pending proposals (L3 lands sprint 10)" while it waits for
 * the first event to arrive.
 */

export type ProposalKind =
  | "weight_delta"
  | "new_signal_idea"
  | "prompt_tweak";

/**
 * Lifecycle status — sprint_10 (T-B-030 schema v0.2.0). Producer-side
 * proposals always land as "pending"; the FastAPI approve/reject routes
 * (T-B-031) APPEND a new line with the same `proposal_id` carrying a
 * non-pending status. The dashboard folds latest-status-wins so the
 * Pending tab shows whose latest line is "pending" and the History tab
 * shows everyone else.
 *
 * Absent `status` on the wire is interpreted as "pending" (mirrors the
 * backend `_fold_pending_proposals_from_jsonl` semantics — schema v0.1.0
 * producers that omit the field stay forward-compatible).
 */
export type ProposalStatus = "pending" | "approved" | "rejected";

/**
 * Mirrors strategy_proposal_schema.v0.1.0 — fields are PRECISELY those the
 * schema marks `required` (top of file), plus the documented optional
 * extension fields. `proposed_change` is the schema's open-ended payload
 * — we model it as `Record<string, unknown>` so each `kind` can sub-type
 * via discriminated union below.
 */
export interface StrategyProposal {
  readonly proposal_id: string;
  readonly ts: string; // ISO-8601 UTC
  readonly kind: ProposalKind | string; // string fallback for forward-compat
  readonly rationale: string;
  readonly proposed_change?: Record<string, unknown>;
  readonly expected_impact?: string | null;
  readonly confidence_pct: number;
  readonly requires_human_approval: boolean;
  /**
   * Optional in v0.1.0 (producers omit it); locked vocabulary in v0.2.0.
   * Absent → treat as `"pending"`. The dashboard's `foldByLatestStatus`
   * helper does that interpretation in one place so component code can
   * branch on a non-nullable status.
   */
  readonly status?: ProposalStatus;
}

/** Typed `proposed_change` for kind === "weight_delta". */
export interface WeightDeltaChange {
  readonly key: string;
  readonly delta: number;
}

/** Typed `proposed_change` for kind === "new_signal_idea". */
export interface NewSignalIdeaChange {
  readonly name: string;
  readonly primary_features?: ReadonlyArray<string>;
  readonly fusion_layer?: string;
}

/** Typed `proposed_change` for kind === "prompt_tweak". */
export interface PromptTweakChange {
  readonly prompt_file: string;
  readonly diff_summary: string;
}

/** Narrowing helper — true iff the object looks like a v0.1.0 proposal. */
export function isStrategyProposal(value: unknown): value is StrategyProposal {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.proposal_id === "string" &&
    typeof v.ts === "string" &&
    typeof v.kind === "string" &&
    typeof v.rationale === "string" &&
    typeof v.confidence_pct === "number" &&
    typeof v.requires_human_approval === "boolean"
  );
}

/**
 * Parse a JSONL string (typical content of `proposals.jsonl`) into an array
 * of typed proposals. Lines that don't parse / don't validate are SILENTLY
 * dropped — the JSONL stream is append-only and a partial/torn line at the
 * tail is normal during a live write; we'll see it next poll.
 */
export function parseProposalsJsonl(jsonl: string): StrategyProposal[] {
  const out: StrategyProposal[] = [];
  for (const raw of jsonl.split("\n")) {
    const line = raw.trim();
    if (line.length === 0) continue;
    let parsed: unknown;
    try {
      parsed = JSON.parse(line);
    } catch {
      continue;
    }
    if (isStrategyProposal(parsed)) {
      out.push(parsed);
    }
  }
  return out;
}

/** Order newest-first so the dashboard renders the most recent at top. */
export function sortNewestFirst(
  proposals: ReadonlyArray<StrategyProposal>,
): StrategyProposal[] {
  return [...proposals].sort((a, b) => b.ts.localeCompare(a.ts));
}

/**
 * Default history-tab page size — the brief locks this to 20.
 *
 * Why 20: at typical L3-advisor cadence (~1 proposal per 100 ticks ≈ minutes
 * to hours apart) twenty rows comfortably covers the operator's working
 * session without scrolling fatigue. The Demo Readiness Reviewer cares
 * about this number — the brief calls it out explicitly.
 */
export const HISTORY_PAGE_SIZE = 20;

/**
 * Resolve a proposal's effective status. v0.1.0 producers don't carry
 * `status` on the wire — those rows are treated as `"pending"` so the
 * Pending tab keeps showing them until the operator (or a v0.2.0 producer
 * row with status="approved"/"rejected") changes the verdict.
 */
export function effectiveStatus(p: StrategyProposal): ProposalStatus {
  return p.status ?? "pending";
}

/**
 * Fold-latest-status-wins over a sequence of proposal rows — the SAME
 * semantic the backend `_fold_pending_proposals_from_jsonl` helper applies
 * on agent restart. Each `proposal_id` collapses to its LAST row in input
 * order; ties broken by `ts` (newest wins). Used by the dashboard to turn
 * the SSE event-stream (which carries every status transition as a
 * separate event) into a stable per-proposal view.
 *
 * Two outputs:
 *
 *   - `pending`  — proposals whose latest effective status is `"pending"`,
 *                  newest-first so the dashboard renders fresh proposals
 *                  at the top of the Pending tab.
 *   - `history`  — every non-pending proposal, newest-first, truncated to
 *                  `historyLimit` (default {@link HISTORY_PAGE_SIZE}) so
 *                  the History tab is bounded.
 */
export interface FoldedProposals {
  readonly pending: ReadonlyArray<StrategyProposal>;
  readonly history: ReadonlyArray<StrategyProposal>;
}

export function foldByLatestStatus(
  proposals: ReadonlyArray<StrategyProposal>,
  historyLimit: number = HISTORY_PAGE_SIZE,
): FoldedProposals {
  // `Map` preserves insertion order — we sort by ts ASC first so the LAST
  // entry per id is the freshest row. Then collapse and partition.
  const sorted = [...proposals].sort((a, b) => a.ts.localeCompare(b.ts));
  const latest = new Map<string, StrategyProposal>();
  for (const row of sorted) {
    latest.set(row.proposal_id, row);
  }
  const collapsed = [...latest.values()];
  const pending: StrategyProposal[] = [];
  const history: StrategyProposal[] = [];
  for (const row of collapsed) {
    if (effectiveStatus(row) === "pending") pending.push(row);
    else history.push(row);
  }
  // Both lists are rendered newest-first.
  pending.sort((a, b) => b.ts.localeCompare(a.ts));
  history.sort((a, b) => b.ts.localeCompare(a.ts));
  return {
    pending,
    history: history.slice(0, Math.max(0, historyLimit)),
  };
}

/**
 * Human-readable summary line — the dashboard card uses this when the user
 * hasn't expanded the proposal. Length-bounded to ~120 chars so the
 * collapsed card stays one line at typical viewport widths.
 */
export function summariseProposal(p: StrategyProposal): string {
  switch (p.kind) {
    case "weight_delta": {
      const ch = (p.proposed_change ?? {}) as Partial<WeightDeltaChange>;
      const delta = typeof ch.delta === "number"
        ? (ch.delta >= 0 ? `+${ch.delta}` : String(ch.delta))
        : "?";
      return `bump ${ch.key ?? "?"} by ${delta}`;
    }
    case "new_signal_idea": {
      const ch = (p.proposed_change ?? {}) as Partial<NewSignalIdeaChange>;
      return `add engine ${ch.name ?? "?"}${ch.fusion_layer ? ` → ${ch.fusion_layer}` : ""}`;
    }
    case "prompt_tweak": {
      const ch = (p.proposed_change ?? {}) as Partial<PromptTweakChange>;
      return `patch prompt ${ch.prompt_file ?? "?"}`;
    }
    default:
      return p.rationale.slice(0, 120);
  }
}
