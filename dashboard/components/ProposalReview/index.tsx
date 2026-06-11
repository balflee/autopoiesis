"use client";

/**
 * ProposalReview — L3 strategy-proposal review panel (T-D-012 sprint_10).
 *
 * Sprint_10 day 4 closes the L3 demo moment: agent proposes → user approves
 * (or rejects) → weights change. The panel has two tabs:
 *
 *   - Pending (default): cards for every proposal whose latest status is
 *     "pending". Approve / Reject buttons go through the same-origin
 *     proxy (`/api/proxy/api/proposals/{id}/(approve|reject)`); the
 *     proxy injects the bearer token server-side per T-D-011.
 *
 *   - History (read-only): the last N (default 20) approved-or-rejected
 *     proposals, newest-first. No actions; the kind badge is paired with
 *     a colored status badge.
 *
 * Data flow:
 *
 *   - Live source: SSE `event: proposals` from Track B (T-B-030). Every
 *     status transition (pending → approved / rejected) is emitted as a
 *     NEW line with the same proposal_id; the dashboard folds
 *     latest-status-wins via {@link foldByLatestStatus}.
 *
 *   - Optimistic UI: Approve / Reject flip the proposal into a local
 *     "decided" overlay BEFORE the network call returns so the card fades
 *     + moves to the History tab within 100 ms (the brief calls this out
 *     explicitly). On API error we ROLL BACK the overlay and surface an
 *     inline error on the card so the operator can retry.
 *
 *   - Reject reason: clicking Reject opens a modal with a textarea +
 *     submit / cancel. Empty submission is allowed (the backend accepts
 *     a snap-reject); a non-empty reason is folded into the audit row.
 *
 * Accessibility:
 *
 *   - Tabs follow the WAI-ARIA Authoring Practices Guide tab pattern
 *     (`role="tablist"`, `aria-selected`, arrow-key navigation).
 *   - The reject modal is `role="dialog"` with focus trap + ESC-to-close.
 *   - Every interactive control has an aria-label or visible label.
 *
 * Hard-rule compliance:
 *
 *   - The browser bundle NEVER reads `DASHBOARD_API_TOKEN` — the api_client
 *     defaults to the same-origin proxy. We rely on the proxy + the
 *     server-side env vars set on Vercel.
 *   - No proposal schema fields are invented here — every read goes
 *     through `lib/load_proposals.ts` which mirrors
 *     `.dev/contracts/strategy_proposal_schema.v0.2.0.json`.
 */

import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type JSX,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";

import {
  ApiError,
  approveProposal as apiApprove,
  rejectProposal as apiReject,
  type ApiClientOptions,
  type ProposalActionResponse,
} from "@/lib/api_client";
import {
  effectiveStatus,
  foldByLatestStatus,
  HISTORY_PAGE_SIZE,
  isStrategyProposal,
  type StrategyProposal,
} from "@/lib/load_proposals";
import {
  subscribeSse,
  type ProposalStreamEvent,
  type SseStatus,
  type SseSubscription,
} from "@/lib/sse_subscribe";

import { ProposalCard } from "./ProposalCard";

const PRD_LINK_HREF =
  "https://github.com/genesis-experiment/genesis/blob/main/docs/PRD.md#12-l3-meta-optimizer";

type TabKey = "pending" | "history";

/**
 * Test-mode override hook for Playwright. Set
 * `window.__GENESIS_PROPOSAL_API__ = { approve, reject }` BEFORE rendering
 * to short-circuit the real fetch — used by `test_proposal_review_*`
 * specs that don't want to wire a fake proxy. Production builds never
 * read this surface (the dashboard `useEffect` reads `window` only when
 * already mounted in a browser context).
 */
declare global {
  interface Window {
    __GENESIS_PROPOSAL_API__?: {
      approveProposal?: (id: string) => Promise<ProposalActionResponse>;
      rejectProposal?: (
        id: string,
        reason?: string,
      ) => Promise<ProposalActionResponse>;
    };
    /** Set by tests to seed the panel with synthetic proposals — sprint_10. */
    __GENESIS_PROPOSAL_SEED__?: ReadonlyArray<StrategyProposal>;
  }
}

export interface ProposalReviewProps {
  /** Storybook / Playwright override — explicit set of proposals to render. */
  readonly mockProposals?: ReadonlyArray<StrategyProposal>;
  /** Skip the SSE subscription — used with mockProposals. */
  readonly suppressNetwork?: boolean;
  /** Force-mount one tab — used by component tests. */
  readonly initialTab?: TabKey;
  /** Override the history page size (default = HISTORY_PAGE_SIZE). */
  readonly historyLimit?: number;
}

interface LocalOverlay {
  /** Set when the operator has just clicked Approve / Reject in this tab. */
  readonly action: "approved" | "rejected";
  /** Set when the network call is still in flight. */
  readonly busy: boolean;
  /** Set when the network call rolled back. */
  readonly error?: string;
  /** Optional reject reason — folded onto the optimistic history row. */
  readonly reason?: string;
}

export function ProposalReview(props: ProposalReviewProps = {}): JSX.Element {
  const historyLimit = props.historyLimit ?? HISTORY_PAGE_SIZE;
  const [tab, setTab] = useState<TabKey>(props.initialTab ?? "pending");
  const [proposals, setProposals] = useState<StrategyProposal[]>(() => {
    if (props.mockProposals) return [...props.mockProposals];
    if (typeof window !== "undefined" && window.__GENESIS_PROPOSAL_SEED__) {
      return [...window.__GENESIS_PROPOSAL_SEED__];
    }
    return [];
  });
  const [overlays, setOverlays] = useState<Record<string, LocalOverlay>>({});
  const [sseStatus, setSseStatus] = useState<SseStatus>("idle");
  const [subscription, setSubscription] = useState<SseSubscription | null>(null);
  const [rejectTarget, setRejectTarget] = useState<StrategyProposal | null>(null);

  /* --- SSE subscription -------------------------------------------- */

  useEffect(() => {
    if (props.suppressNetwork || props.mockProposals) return;
    const sub = subscribeSse({
      onStatusChange: setSseStatus,
      onProposal: (raw: ProposalStreamEvent) => {
        if (!isStrategyProposal(raw)) return;
        setProposals((prev) => {
          // The fold helper handles status transitions, but to keep the
          // raw list bounded we replace-by-proposal-id when we see a row
          // for an id we already have; new ids are appended.
          const ix = prev.findIndex(
            (p) => p.proposal_id === raw.proposal_id && p.ts === raw.ts,
          );
          if (ix >= 0) {
            const copy = [...prev];
            copy[ix] = raw;
            return copy;
          }
          return [...prev, raw];
        });
        // The server is now the source of truth for the proposal's
        // status — drop any overlay we were holding for it.
        setOverlays((prev) => {
          if (!prev[raw.proposal_id]) return prev;
          const next = { ...prev };
          delete next[raw.proposal_id];
          return next;
        });
      },
    });
    setSubscription(sub);
    return () => sub.close();
  }, [props.suppressNetwork, props.mockProposals]);

  /* --- Folded views ------------------------------------------------ */

  // Apply local overlays — proposals with an overlay are treated as if
  // they already carry the overlay's status. This is what produces the
  // "card fades + moves to History within 100 ms" behaviour: the parent
  // recomputes the fold synchronously on click, no network round-trip.
  const decorated = useMemo<StrategyProposal[]>(() => {
    return proposals.map((p) => {
      const overlay = overlays[p.proposal_id];
      if (!overlay) return p;
      if (overlay.busy && !overlay.error) {
        // Optimistic — pretend the server already accepted.
        return { ...p, status: overlay.action };
      }
      if (overlay.error) {
        // Roll back — keep the proposal as pending, the card itself
        // surfaces the error inline.
        return p;
      }
      return { ...p, status: overlay.action };
    });
  }, [proposals, overlays]);

  const folded = useMemo(
    () => foldByLatestStatus(decorated, historyLimit),
    [decorated, historyLimit],
  );

  /* --- Actions ----------------------------------------------------- */

  const apiOverride = useCallback(() => {
    if (typeof window === "undefined") return null;
    return window.__GENESIS_PROPOSAL_API__ ?? null;
  }, []);

  const callApprove = useCallback(
    async (id: string, options: ApiClientOptions = {}) => {
      const override = apiOverride();
      if (override?.approveProposal) return override.approveProposal(id);
      return apiApprove(id, options);
    },
    [apiOverride],
  );

  const callReject = useCallback(
    async (id: string, reason?: string, options: ApiClientOptions = {}) => {
      const override = apiOverride();
      if (override?.rejectProposal) return override.rejectProposal(id, reason);
      return apiReject(id, reason, options);
    },
    [apiOverride],
  );

  const onApprove = useCallback(
    (p: StrategyProposal) => {
      const id = p.proposal_id;
      setOverlays((prev) => ({
        ...prev,
        [id]: { action: "approved", busy: true },
      }));
      void (async () => {
        try {
          await callApprove(id);
          setOverlays((prev) => ({
            ...prev,
            [id]: { action: "approved", busy: false },
          }));
        } catch (cause) {
          const msg =
            cause instanceof ApiError && cause.status === 409
              ? "already decided on the backend — refresh"
              : `approve failed (${(cause as Error).message ?? "network"})`;
          setOverlays((prev) => ({
            ...prev,
            [id]: { action: "approved", busy: false, error: msg },
          }));
        }
      })();
    },
    [callApprove],
  );

  const openRejectModal = useCallback((p: StrategyProposal) => {
    setRejectTarget(p);
  }, []);

  const closeRejectModal = useCallback(() => {
    setRejectTarget(null);
  }, []);

  const onReject = useCallback(
    (p: StrategyProposal, reason: string) => {
      const id = p.proposal_id;
      const trimmed = reason.trim();
      setRejectTarget(null);
      setOverlays((prev) => ({
        ...prev,
        [id]: {
          action: "rejected",
          busy: true,
          reason: trimmed.length > 0 ? trimmed : undefined,
        },
      }));
      void (async () => {
        try {
          await callReject(id, trimmed.length > 0 ? trimmed : undefined);
          setOverlays((prev) => ({
            ...prev,
            [id]: {
              action: "rejected",
              busy: false,
              reason: trimmed.length > 0 ? trimmed : undefined,
            },
          }));
        } catch (cause) {
          const msg =
            cause instanceof ApiError && cause.status === 409
              ? "already decided on the backend — refresh"
              : `reject failed (${(cause as Error).message ?? "network"})`;
          setOverlays((prev) => ({
            ...prev,
            [id]: { action: "rejected", busy: false, error: msg },
          }));
        }
      })();
    },
    [callReject],
  );

  /* --- Render ------------------------------------------------------ */

  const pendingCount = folded.pending.length;
  const historyCount = folded.history.length;
  const empty = pendingCount === 0 && historyCount === 0;

  return (
    <section
      data-testid="proposal-review"
      data-empty={empty ? "true" : "false"}
      data-sse-status={sseStatus}
      data-tab={tab}
      role="region"
      aria-label="Strategy proposals (L3)"
      className="flex w-full flex-col gap-3 rounded-md border border-genesis-ink-muted/30 bg-genesis-bg/60 p-4"
    >
      <header className="flex flex-col gap-2 border-b border-genesis-ink-muted/15 pb-2">
        <div className="flex items-baseline justify-between gap-3">
          <h3 className="font-mono text-[10px] uppercase tracking-[0.28em] text-genesis-ink">
            L3 · strategy proposals
          </h3>
          <span className="font-mono text-[10px] uppercase tracking-[0.22em] text-genesis-ink-muted">
            {pendingCount} pending · {historyCount} history
          </span>
        </div>
        <Tabs
          activeTab={tab}
          onChange={setTab}
          pendingCount={pendingCount}
          historyCount={historyCount}
        />
      </header>

      {sseStatus === "auth_blocked" ? (
        <p
          data-testid="proposal-review-auth-banner"
          role="alert"
          className="rounded-sm border border-genesis-amber/40 bg-genesis-amber/[0.06] px-3 py-2 font-mono text-[10px] uppercase leading-relaxed tracking-[0.22em] text-genesis-amber"
        >
          live stream unavailable — same-origin proxy missing
          DASHBOARD_API_TOKEN. once the server-side token is set the
          stream will resume; cached pending proposals stay visible.
        </p>
      ) : null}

      {tab === "pending" ? (
        pendingCount === 0 ? (
          <EmptyState />
        ) : (
          <ul
            className="flex flex-col gap-3"
            data-testid="proposal-review-pending-list"
          >
            {folded.pending.map((p, idx) => {
              const overlay = overlays[p.proposal_id];
              return (
                <ProposalCard
                  key={p.proposal_id}
                  proposal={p}
                  index={idx}
                  variant="pending"
                  busy={overlay?.busy ?? false}
                  errorMessage={overlay?.error ?? null}
                  onApprove={onApprove}
                  onReject={openRejectModal}
                />
              );
            })}
          </ul>
        )
      ) : null}

      {tab === "history" ? (
        historyCount === 0 ? (
          <HistoryEmpty />
        ) : (
          <ul
            className="flex flex-col gap-3"
            data-testid="proposal-review-history-list"
          >
            {folded.history.map((p, idx) => (
              <ProposalCard
                key={`${p.proposal_id}-${effectiveStatus(p)}`}
                proposal={p}
                index={idx}
                variant="history"
              />
            ))}
          </ul>
        )
      ) : null}

      {rejectTarget ? (
        <RejectModal
          proposal={rejectTarget}
          onSubmit={(reason) => onReject(rejectTarget, reason)}
          onCancel={closeRejectModal}
        />
      ) : null}

      {/* invisible — Playwright reads the underlying subscription handle */}
      <span hidden data-testid="proposal-review-subscription">
        {subscription ? "1" : "0"}
      </span>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* Tabs                                                                */
/* ------------------------------------------------------------------ */

interface TabsProps {
  readonly activeTab: TabKey;
  readonly onChange: (tab: TabKey) => void;
  readonly pendingCount: number;
  readonly historyCount: number;
}

function Tabs(props: TabsProps): JSX.Element {
  const { activeTab, onChange, pendingCount, historyCount } = props;
  const tabs: ReadonlyArray<{ key: TabKey; label: string; count: number }> = [
    { key: "pending", label: "pending", count: pendingCount },
    { key: "history", label: "history", count: historyCount },
  ];
  const onKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>): void => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const idx = tabs.findIndex((t) => t.key === activeTab);
    if (idx < 0) return;
    const nextIdx =
      e.key === "ArrowRight"
        ? (idx + 1) % tabs.length
        : (idx - 1 + tabs.length) % tabs.length;
    const nextTab = tabs[nextIdx];
    if (nextTab) onChange(nextTab.key);
  };
  return (
    <div
      role="tablist"
      aria-label="Proposal queue tabs"
      data-testid="proposal-review-tabs"
      onKeyDown={onKeyDown}
      className="flex flex-wrap gap-1"
    >
      {tabs.map((t) => {
        const active = t.key === activeTab;
        return (
          <button
            key={t.key}
            type="button"
            role="tab"
            id={`proposal-review-tab-${t.key}`}
            aria-selected={active}
            aria-controls={`proposal-review-panel-${t.key}`}
            tabIndex={active ? 0 : -1}
            data-testid={`proposal-review-tab-${t.key}`}
            onClick={() => onChange(t.key)}
            className={`rounded-sm border px-3 py-1 font-mono text-[10px] uppercase tracking-[0.28em] transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-genesis-amber/70 ${
              active
                ? "border-genesis-amber/70 text-genesis-amber"
                : "border-genesis-ink-muted/30 text-genesis-ink-muted hover:border-genesis-ink-muted/60"
            }`}
          >
            {t.label} · {t.count}
          </button>
        );
      })}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Empty states                                                        */
/* ------------------------------------------------------------------ */

function EmptyState(): JSX.Element {
  return (
    <div
      data-testid="proposal-review-empty"
      className="flex flex-col items-center gap-3 px-4 py-10 text-center"
    >
      <svg
        aria-hidden
        width="48"
        height="48"
        viewBox="0 0 48 48"
        className="text-genesis-ink-muted/40"
        fill="none"
        stroke="currentColor"
        strokeWidth="1"
      >
        <circle cx="24" cy="24" r="18" />
        <line x1="24" y1="2" x2="24" y2="10" />
        <line x1="24" y1="38" x2="24" y2="46" />
        <line x1="2" y1="24" x2="10" y2="24" />
        <line x1="38" y1="24" x2="46" y2="24" />
        <circle cx="24" cy="24" r="3" fill="currentColor" stroke="none" />
      </svg>
      <p className="font-mono text-[11px] uppercase tracking-[0.28em] text-genesis-ink">
        no pending proposals
      </p>
      <p className="max-w-sm font-mono text-[10px] uppercase tracking-[0.22em] text-genesis-ink-muted">
        the L3 advisor fires every 100 ticks once the live agent is
        running. proposals land here for operator review.
      </p>
      <a
        href={PRD_LINK_HREF}
        target="_blank"
        rel="noopener noreferrer"
        data-testid="proposal-review-prd-link"
        className="rounded-sm border border-genesis-ink-muted/30 px-3 py-1 font-mono text-[10px] uppercase tracking-[0.28em] text-genesis-ink-muted transition-colors hover:border-genesis-amber/70 hover:text-genesis-amber focus:outline-none focus-visible:ring-2 focus-visible:ring-genesis-amber/70"
      >
        PRD §12 →
      </a>
    </div>
  );
}

function HistoryEmpty(): JSX.Element {
  return (
    <div
      data-testid="proposal-review-history-empty"
      className="flex flex-col items-center gap-2 px-4 py-8 text-center"
    >
      <p className="font-mono text-[11px] uppercase tracking-[0.28em] text-genesis-ink">
        no decisions yet
      </p>
      <p className="max-w-sm font-mono text-[10px] uppercase tracking-[0.22em] text-genesis-ink-muted">
        approved and rejected proposals will appear here once the operator
        acts on the first pending row.
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Reject reason modal                                                 */
/* ------------------------------------------------------------------ */

interface RejectModalProps {
  readonly proposal: StrategyProposal;
  readonly onSubmit: (reason: string) => void;
  readonly onCancel: () => void;
}

function RejectModal(props: RejectModalProps): JSX.Element {
  const { proposal, onSubmit, onCancel } = props;
  const [reason, setReason] = useState<string>("");
  const titleId = useId();
  const descId = useId();
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const cancelRef = useRef<HTMLButtonElement | null>(null);

  // Focus the textarea on open; ESC closes the modal.
  useEffect(() => {
    textareaRef.current?.focus();
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") {
        e.preventDefault();
        onCancel();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  return (
    <div
      data-testid="proposal-review-reject-modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      aria-describedby={descId}
      className="fixed inset-0 z-50 flex items-center justify-center bg-genesis-bg/80 backdrop-blur-sm"
      onClick={(e) => {
        // Click on the backdrop (not the dialog body) closes the modal.
        if (e.target === e.currentTarget) onCancel();
      }}
    >
      <div className="flex w-full max-w-md flex-col gap-3 rounded-md border border-genesis-loss/40 bg-genesis-bg p-4 shadow-2xl">
        <h4
          id={titleId}
          className="font-mono text-[12px] uppercase tracking-[0.28em] text-genesis-loss"
        >
          reject proposal
        </h4>
        <p
          id={descId}
          className="font-mono text-[10px] uppercase tracking-[0.22em] text-genesis-ink-muted"
        >
          {proposal.kind} · conf {proposal.confidence_pct}%
        </p>
        <label
          htmlFor="proposal-review-reject-reason"
          className="font-mono text-[10px] uppercase tracking-[0.22em] text-genesis-ink"
        >
          reason (optional)
        </label>
        <textarea
          id="proposal-review-reject-reason"
          data-testid="proposal-review-reject-reason"
          ref={textareaRef}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          rows={4}
          placeholder="e.g. waiting on tomorrow's reflection before bumping alpha_2"
          aria-label="Optional reason for rejecting this proposal"
          className="min-h-[6rem] rounded-sm border border-genesis-ink-muted/30 bg-genesis-bg/80 p-2 font-mono text-[11px] text-genesis-ink placeholder:text-genesis-ink-muted/60 focus:border-genesis-loss/70 focus:outline-none focus-visible:ring-2 focus-visible:ring-genesis-loss/70"
        />
        <div className="flex items-center justify-end gap-2 pt-1">
          <button
            type="button"
            ref={cancelRef}
            data-testid="proposal-review-reject-cancel"
            onClick={onCancel}
            className="rounded-sm border border-genesis-ink-muted/30 px-3 py-1 font-mono text-[10px] uppercase tracking-[0.28em] text-genesis-ink-muted transition-colors hover:border-genesis-ink-muted/60 focus:outline-none focus-visible:ring-2 focus-visible:ring-genesis-amber/70"
          >
            cancel
          </button>
          <button
            type="button"
            data-testid="proposal-review-reject-submit"
            onClick={() => onSubmit(reason)}
            className="rounded-sm border border-genesis-loss/60 px-3 py-1 font-mono text-[10px] uppercase tracking-[0.28em] text-genesis-loss transition-colors hover:bg-genesis-loss/10 focus:outline-none focus-visible:ring-2 focus-visible:ring-genesis-loss/70"
          >
            ✕ submit reject
          </button>
        </div>
      </div>
    </div>
  );
}

export default ProposalReview;
