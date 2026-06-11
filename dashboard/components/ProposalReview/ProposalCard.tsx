"use client";

/**
 * ProposalCard — one row in the L3 ProposalReview Pending / History tabs.
 *
 * T-D-012 sprint_10 — the brief's per-card surface:
 *
 *   - kind badge (`weight_delta` | `new_signal_idea` | `prompt_tweak`)
 *   - rationale (2–3 lines, line-clamped)
 *   - `proposed_change` as syntax-highlighted JSON
 *     (collapsible when > 5 lines)
 *   - `expected_impact` chip
 *   - `confidence_pct` horizontal bar (0..100)
 *   - ts (right-aligned, HH:MM:SS)
 *   - Approve + Reject buttons (Pending tab only; History tab is read-only
 *     and stamps the status badge on the right where the actions would
 *     otherwise sit)
 *
 * Accessibility:
 *
 *   - Each action button carries an `aria-label` that includes the proposal
 *     summary so screen readers pronounce "Approve proposal: bump alpha_2
 *     by +0.06" instead of just "Approve".
 *   - The `pending` / `approved` / `rejected` status is stamped on the
 *     card via `data-status` so e2e specs can assert without DOM-walking.
 *   - The JSON `proposed_change` block is in a `<pre>` with `aria-label`
 *     so a screen reader can locate it; the collapse / expand toggle is a
 *     `button` with `aria-expanded`.
 *
 * The card itself is a presentational component — all state (loading
 * spinners, optimistic moves, reject-reason modal) lives in
 * `ProposalReview/index.tsx` so this file stays pure and easy to test.
 */

import { useMemo, useState, type JSX } from "react";

import {
  effectiveStatus,
  summariseProposal,
  type ProposalStatus,
  type StrategyProposal,
} from "@/lib/load_proposals";

/**
 * Threshold at which the JSON `proposed_change` block collapses by default.
 * The brief locks this to "collapsible >5 lines"; cards with shorter
 * payloads stay fully expanded so the operator doesn't have to chase a
 * disclosure caret to read three lines of JSON.
 */
const JSON_COLLAPSE_THRESHOLD_LINES = 5;

const KIND_LABEL: Record<string, string> = {
  weight_delta: "weight delta",
  new_signal_idea: "new signal",
  prompt_tweak: "prompt tweak",
};

/**
 * Kind-specific accent color — matches the dashboard's existing semantic
 * palette so weight_delta (the auto-applied kind) reads as "active" and
 * the manual-processing kinds (new_signal_idea / prompt_tweak) read as
 * "informational".
 */
const KIND_ACCENT: Record<string, string> = {
  weight_delta: "border-genesis-amber/60 text-genesis-amber",
  new_signal_idea: "border-genesis-win/60 text-genesis-win",
  prompt_tweak: "border-genesis-ink-muted/60 text-genesis-ink",
};

export interface ProposalCardProps {
  readonly proposal: StrategyProposal;
  readonly index: number;
  /** Tab the card is rendered in; controls action-row vs status-row footer. */
  readonly variant: "pending" | "history";
  /** Optimistic-update marker — fades the card while the network is pending. */
  readonly busy?: boolean;
  /** Optional error string surfaced inline when the optimistic update rolled back. */
  readonly errorMessage?: string | null;
  /** Approve handler — Pending variant only. No-op when undefined. */
  readonly onApprove?: (p: StrategyProposal) => void;
  /** Reject handler — Pending variant only. Opens the reason modal in the parent. */
  readonly onReject?: (p: StrategyProposal) => void;
}

export function ProposalCard(props: ProposalCardProps): JSX.Element {
  const { proposal, index, variant, busy, errorMessage, onApprove, onReject } = props;
  const status = effectiveStatus(proposal);
  const kindLabel = KIND_LABEL[proposal.kind] ?? proposal.kind;
  const kindAccent = KIND_ACCENT[proposal.kind] ?? KIND_ACCENT.prompt_tweak;
  const summary = summariseProposal(proposal);

  // The JSON block is computed once per render — JSON.stringify is fine on
  // the small `proposed_change` payloads the schema carries (a half-dozen
  // keys at most). Memoised because we read its line count to decide on
  // the default-collapsed state below.
  const json = useMemo(
    () => JSON.stringify(proposal.proposed_change ?? {}, null, 2),
    [proposal.proposed_change],
  );
  const jsonLines = useMemo(() => json.split("\n").length, [json]);
  const collapsible = jsonLines > JSON_COLLAPSE_THRESHOLD_LINES;
  const [expanded, setExpanded] = useState<boolean>(!collapsible);

  const testIdBase = `proposal-card-${index}`;
  const isPending = variant === "pending";
  const ariaSummary = `${kindLabel}: ${summary}`;

  return (
    <li
      data-testid={testIdBase}
      data-proposal-id={proposal.proposal_id}
      data-status={status}
      data-variant={variant}
      data-kind={proposal.kind}
      data-busy={busy ? "true" : "false"}
      className={`flex flex-col gap-2 rounded-sm border bg-genesis-bg/60 p-3 transition-opacity ${
        busy
          ? "opacity-50 border-genesis-amber/30"
          : "opacity-100 border-genesis-ink-muted/15"
      }`}
    >
      <header className="flex flex-wrap items-baseline gap-2">
        <span
          data-testid={`${testIdBase}-kind`}
          className={`rounded-sm border px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.22em] ${kindAccent}`}
        >
          {kindLabel}
        </span>
        {!isPending ? (
          <span
            data-testid={`${testIdBase}-status`}
            className={`rounded-sm border px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.22em] ${
              status === "approved"
                ? "border-genesis-win/60 text-genesis-win"
                : "border-genesis-loss/60 text-genesis-loss"
            }`}
          >
            {status}
          </span>
        ) : null}
        <span
          data-testid={`${testIdBase}-conf`}
          className="font-mono text-[10px] uppercase tracking-[0.22em] text-genesis-ink-muted tabular-nums"
        >
          conf · {proposal.confidence_pct}%
        </span>
        <time
          dateTime={proposal.ts}
          className="ml-auto font-mono text-[10px] uppercase tracking-[0.18em] text-genesis-ink-muted/70 tabular-nums"
        >
          {proposal.ts.slice(11, 19)}
        </time>
      </header>

      <p
        data-testid={`${testIdBase}-summary`}
        className="font-serif-display text-[15px] leading-snug text-genesis-ink"
      >
        {summary}
      </p>

      <p
        data-testid={`${testIdBase}-rationale`}
        className="line-clamp-3 font-mono text-[11px] leading-relaxed text-genesis-ink-muted"
      >
        {proposal.rationale}
      </p>

      <ConfidenceBar value={proposal.confidence_pct} testId={`${testIdBase}-bar`} />

      {proposal.expected_impact ? (
        <p
          data-testid={`${testIdBase}-impact`}
          className="font-mono text-[10px] uppercase tracking-[0.22em] text-genesis-win"
        >
          impact · {proposal.expected_impact}
        </p>
      ) : null}

      <div className="flex flex-col gap-1">
        <div className="flex items-baseline justify-between gap-2">
          <span className="font-mono text-[10px] uppercase tracking-[0.22em] text-genesis-ink-muted">
            proposed_change
          </span>
          {collapsible ? (
            <button
              type="button"
              data-testid={`${testIdBase}-payload-toggle`}
              aria-expanded={expanded}
              aria-controls={`${testIdBase}-payload`}
              onClick={() => setExpanded((p) => !p)}
              className="rounded-sm border border-genesis-ink-muted/30 px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.22em] text-genesis-ink-muted transition-colors hover:border-genesis-ink-muted/60 focus:outline-none focus-visible:ring-2 focus-visible:ring-genesis-amber/70"
            >
              {expanded ? "▾ collapse" : `▸ ${jsonLines} lines`}
            </button>
          ) : null}
        </div>
        {expanded ? (
          <SyntaxHighlightedJson
            json={json}
            testId={`${testIdBase}-payload`}
            aria-label={`Proposed change for ${ariaSummary}`}
          />
        ) : null}
      </div>

      {errorMessage ? (
        <p
          data-testid={`${testIdBase}-error`}
          role="alert"
          className="rounded-sm border border-genesis-loss/40 bg-genesis-loss/[0.06] px-2 py-1 font-mono text-[10px] uppercase tracking-[0.22em] text-genesis-loss"
        >
          {errorMessage}
        </p>
      ) : null}

      {isPending ? (
        <footer className="flex flex-wrap items-center gap-2 pt-1">
          <button
            type="button"
            data-testid={`${testIdBase}-approve`}
            aria-label={`Approve proposal: ${ariaSummary}`}
            disabled={busy}
            onClick={() => onApprove?.(proposal)}
            className="rounded-sm border border-genesis-win/60 px-3 py-1 font-mono text-[10px] uppercase tracking-[0.28em] text-genesis-win transition-colors hover:bg-genesis-win/10 focus:outline-none focus-visible:ring-2 focus-visible:ring-genesis-win/70 disabled:cursor-not-allowed disabled:opacity-40"
          >
            ✓ approve
          </button>
          <button
            type="button"
            data-testid={`${testIdBase}-reject`}
            aria-label={`Reject proposal: ${ariaSummary}`}
            disabled={busy}
            onClick={() => onReject?.(proposal)}
            className="rounded-sm border border-genesis-loss/60 px-3 py-1 font-mono text-[10px] uppercase tracking-[0.28em] text-genesis-loss transition-colors hover:bg-genesis-loss/10 focus:outline-none focus-visible:ring-2 focus-visible:ring-genesis-loss/70 disabled:cursor-not-allowed disabled:opacity-40"
          >
            ✕ reject
          </button>
          {busy ? (
            <span
              data-testid={`${testIdBase}-busy`}
              className="ml-auto font-mono text-[10px] uppercase tracking-[0.22em] text-genesis-ink-muted"
            >
              applying…
            </span>
          ) : null}
        </footer>
      ) : null}
    </li>
  );
}

/* ------------------------------------------------------------------ */
/* Internal — confidence bar                                           */
/* ------------------------------------------------------------------ */

interface ConfidenceBarProps {
  readonly value: number;
  readonly testId: string;
}

function ConfidenceBar({ value, testId }: ConfidenceBarProps): JSX.Element {
  // Clamp defensively — the schema guarantees 0..100 but a malformed feed
  // shouldn't blow the layout.
  const pct = Math.max(0, Math.min(100, Math.round(value)));
  const tone =
    pct >= 70
      ? "bg-genesis-win"
      : pct >= 40
        ? "bg-genesis-amber"
        : "bg-genesis-loss";
  return (
    <div
      role="meter"
      aria-label="proposal confidence"
      aria-valuenow={pct}
      aria-valuemin={0}
      aria-valuemax={100}
      data-testid={testId}
      data-value={pct}
      className="relative h-1.5 w-full overflow-hidden rounded-sm bg-genesis-ink-muted/15"
    >
      <span
        className={`absolute inset-y-0 left-0 ${tone}`}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Internal — JSON syntax highlighter                                  */
/* ------------------------------------------------------------------ */

interface SyntaxHighlightedJsonProps {
  readonly json: string;
  readonly testId: string;
  readonly "aria-label": string;
}

/**
 * Lightweight token-level highlighter — keeps the bundle thin (no prismjs /
 * highlight.js pull) while still reading as "syntax highlighted" per the
 * brief. Tokenises numbers, strings, booleans, null, and keys with a
 * small regex set; everything else falls through as plain text.
 *
 * We're not interpreting the JSON — the input is the already-formatted
 * output of `JSON.stringify(..., null, 2)`. So the tokeniser is
 * order-independent and the regex set is closed.
 */
function SyntaxHighlightedJson(
  props: SyntaxHighlightedJsonProps,
): JSX.Element {
  const tokens = useMemo(() => tokeniseJson(props.json), [props.json]);
  return (
    <pre
      id={props.testId}
      data-testid={props.testId}
      aria-label={props["aria-label"]}
      className="max-h-48 overflow-auto rounded-sm border border-genesis-ink-muted/15 bg-genesis-bg/80 p-2 font-mono text-[10px] leading-snug text-genesis-ink-muted"
    >
      {tokens.map((tok, i) => (
        <span key={i} className={tok.className}>
          {tok.text}
        </span>
      ))}
    </pre>
  );
}

interface JsonToken {
  readonly text: string;
  readonly className: string;
}

const JSON_TOKEN_RE =
  /("(?:\\u[a-fA-F0-9]{4}|\\[^u]|[^\\"])*"(?:\s*:)?|\b(?:true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g;

function tokeniseJson(input: string): JsonToken[] {
  const out: JsonToken[] = [];
  let cursor = 0;
  for (const match of input.matchAll(JSON_TOKEN_RE)) {
    const start = match.index ?? 0;
    if (start > cursor) {
      out.push({ text: input.slice(cursor, start), className: "" });
    }
    const raw = match[0];
    let className = "";
    if (raw.startsWith('"')) {
      className = raw.endsWith(":")
        ? "text-genesis-amber"
        : "text-genesis-win";
    } else if (raw === "true" || raw === "false") {
      className = "text-genesis-amber";
    } else if (raw === "null") {
      className = "text-genesis-ink-muted/60";
    } else {
      className = "text-genesis-amber";
    }
    out.push({ text: raw, className });
    cursor = start + raw.length;
  }
  if (cursor < input.length) {
    out.push({ text: input.slice(cursor), className: "" });
  }
  return out;
}

export default ProposalCard;
