"use client";

import Link from "next/link";
import { useEffect, useState, type JSX } from "react";

/**
 * Landing-page live indicator — proves the mock-bet agent is RUNNING right now.
 *
 * The roadmap (`/`) is a server component (pure CSS, no client hooks), so this
 * is a self-contained client island: it polls the same `/api/sandbox` bundle
 * the dashboard reads (every 5 s — slower than /living's 2 s; it's just a
 * badge) and renders a live "mock-bet LIVE" chip with the agent's state, open
 * position count, and treasury, linking through to the full /living stage.
 *
 * It does NOT mount the SandboxLiveBootstrap/wsStore providers (those live on
 * the dashboard routes) — a standalone fetch keeps the landing a near-static
 * server page with one small live island. Until the first successful fetch (or
 * if the backend is unreachable) it renders NOTHING, so the landing never
 * breaks or flashes a broken state.
 */

const POLL_MS = 5_000;

interface LiveStatus {
  readonly ok: boolean;
  readonly state: "ALIVE" | "DYING" | "DEAD";
  readonly openCount: number;
  readonly treasuryUsd: number;
}

export function LiveMockBetBadge(): JSX.Element | null {
  const [status, setStatus] = useState<LiveStatus | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") return;
    let cancelled = false;
    const tick = async (): Promise<void> => {
      try {
        const res = await fetch("/api/sandbox", { cache: "no-store" });
        if (!res.ok || cancelled) return;
        const d = (await res.json()) as Record<string, unknown>;
        if (cancelled) return;
        const snap = (d.snapshot ?? null) as Record<string, unknown> | null;
        const breath = typeof snap?.breath === "number" ? snap.breath : 0;
        const hasSnap = snap != null;
        const state: LiveStatus["state"] =
          !hasSnap || breath <= 0 ? "DEAD" : breath <= 10 ? "DYING" : "ALIVE";
        const openIds = snap?.open_bet_ids;
        setStatus({
          ok: hasSnap,
          state,
          openCount: Array.isArray(openIds) ? openIds.length : 0,
          treasuryUsd:
            typeof d.gods_revenue_cumulative_usd === "number"
              ? d.gods_revenue_cumulative_usd
              : 0,
        });
      } catch {
        /* keep last-good status; never break the landing */
      }
    };
    void tick();
    const id = setInterval(() => void tick(), POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  // No paint until we have real backend data — keeps the landing clean.
  if (!status || !status.ok) return null;

  const live = status.state === "ALIVE";
  const DEAD_COLOR = "#e0644f"; // coral — no --ab-danger token exists

  return (
    <Link
      href="/living"
      data-testid="live-mockbet-badge"
      className="ab-hero-in mt-8 inline-flex w-fit flex-wrap items-center justify-center gap-2.5 rounded-full border bg-[var(--ab-bg-2)] px-4 py-1.5 font-mono text-[10px] uppercase tracking-[0.22em] transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ab-glow)]/70"
      style={{
        animationDelay: "520ms",
        borderColor: live ? "var(--ab-glow)" : DEAD_COLOR,
      }}
    >
      <span
        className={`h-1.5 w-1.5 rounded-full ${live ? "ab-pulse-dot" : ""}`}
        style={{ background: live ? "var(--ab-glow)" : DEAD_COLOR }}
        aria-hidden
      />
      <span
        className={live ? "ab-glow-text" : ""}
        style={{ color: live ? "var(--ab-glow)" : DEAD_COLOR }}
      >
        mock-bet live
      </span>
      <span className="text-[var(--ab-dim)]" aria-hidden>
        ·
      </span>
      <span className="text-[var(--ab-text)]">agent {status.state}</span>
      <span className="text-[var(--ab-dim)]" aria-hidden>
        ·
      </span>
      <span className="text-[var(--ab-text)]">{status.openCount} open</span>
      <span className="text-[var(--ab-dim)]" aria-hidden>
        ·
      </span>
      <span className="text-[var(--ab-text)]">
        treasury ${status.treasuryUsd.toFixed(0)}
      </span>
      <span className="text-[var(--ab-glow)]/80" aria-hidden>
        ▸ watch
      </span>
    </Link>
  );
}
