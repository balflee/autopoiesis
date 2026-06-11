"use client";

/**
 * PlaybackMode — operator-controlled fixture takeover.
 *
 * Why this exists per TECHNICAL_PLAN.md §12 (Demo 保险动作):
 *
 *   "demo §9 1:30-2:30 使用, 亦可任意时刻手动触发"
 *
 * If the LIVE feed goes silent during the recording window, the operator
 * flips this toggle and a curated 5-minute scenario plays through the
 * same projection components. Because the source is a committed fixture
 * (public/playback_fixtures/golden_scenario_5min.jsonl) every projection
 * the audience sees is a faithful replay of a real Track-B dry run —
 * never an invention of Track D. That's the Permadeath-trustless line
 * TP §12 draws: insurance mechanisms cannot fabricate beats.
 *
 * Anti-fraud invariants (acceptance criteria):
 *
 *   1. While PlaybackMode is engaged the "DEMO PLAYBACK" banner MUST be
 *      visible. The component owns a stable React-rendered HOST div,
 *      and imperatively maintains the banner as a child of that host
 *      inside a useEffect. A MutationObserver + heartbeat detects when
 *      the banner is removed/hidden and re-creates it via raw DOM —
 *      that decouples re-mount from React's reconciler so an externally
 *      removed banner can be re-attached without React throwing on a
 *      stale child reference.
 *   2. The banner z-index sits above every other dashboard surface
 *      (Death Watch + LLM activation overlay both use z-50/z-[60]; the
 *      banner uses z-[9999]).
 *   3. Toggling OFF resets the store but does NOT auto-reconnect to the
 *      LIVE WS — the operator must reload the page to resume LIVE. This
 *      prevents an accidental "I thought we were live" mid-demo.
 *
 * The toggle itself sits upper-right (sticky position, z-[9998]) so the
 * operator can engage it from any scroll position.
 *
 * SSR-safe: all browser APIs (`fetch`, `document`, `MutationObserver`)
 * live inside `useEffect`.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type JSX,
} from "react";

import { isWsMessage, type WsMessage } from "@/lib/types";
import { useWsStore } from "@/lib/wsStore";

const BANNER_TEST_ID = "demo-playback-banner";
const TOGGLE_TEST_ID = "demo-playback-toggle";
const BANNER_DATA_ATTR = "data-genesis-playback-banner";
const DEFAULT_FIXTURE_URL = "/playback_fixtures/golden_scenario_5min.jsonl";

const BANNER_CLASSNAMES =
  "fixed inset-x-0 top-0 z-[9999] flex items-center justify-center " +
  "gap-3 border-b border-genesis-amber/70 bg-genesis-loss/95 " +
  "py-2 text-center text-sm font-extrabold uppercase tracking-[0.2em] " +
  "text-genesis-ink shadow-[0_2px_12px_rgba(230,57,70,0.4)] " +
  "pointer-events-none select-none";

const BANNER_TEXT = "Demo Playback — Not Live";

export interface PlaybackModeProps {
  /**
   * Override the fixture URL. Vitest tests inject a data: URL or a
   * pre-loaded fixture array so they do not need a network.
   */
  readonly fixtureUrl?: string;
  /**
   * Test seam — provide a pre-parsed list of frames instead of fetching.
   * If both `fixtureFrames` and `fixtureUrl` are supplied, the frames
   * win.
   */
  readonly fixtureFrames?: readonly unknown[];
  /**
   * Compression factor for fixture playback. Default 1.0 = real time
   * (the 5-minute scenario takes 5 minutes). Tests pass e.g. 0 for
   * instant ingest.
   */
  readonly timeScale?: number;
  /**
   * Optional override for the initial engaged state — primarily a
   * Storybook hook. Defaults to false (always boots in LIVE mode).
   */
  readonly initialEngaged?: boolean;
}

type EngagedState = "live" | "playback";

/** Banner DOM contract — kept stable so MutationObserver can re-attach. */
function bannerNodeOK(node: HTMLElement | null): boolean {
  if (node === null) return false;
  if (!node.isConnected) return false;
  const cs = node.style;
  if (cs.display === "none") return false;
  if (cs.visibility === "hidden") return false;
  if (cs.opacity !== "" && Number(cs.opacity) < 0.5) return false;
  return true;
}

/** Build the banner DOM imperatively so React's reconciler is not
 *  involved in its lifecycle. */
function createBannerNode(): HTMLDivElement {
  const banner = document.createElement("div");
  banner.setAttribute("data-testid", BANNER_TEST_ID);
  banner.setAttribute(BANNER_DATA_ATTR, "true");
  banner.setAttribute("role", "status");
  banner.setAttribute("aria-live", "polite");
  banner.className = BANNER_CLASSNAMES;
  const dotL = document.createElement("span");
  dotL.setAttribute("aria-hidden", "true");
  dotL.textContent = "●";
  const text = document.createElement("span");
  text.textContent = BANNER_TEXT;
  const dotR = document.createElement("span");
  dotR.setAttribute("aria-hidden", "true");
  dotR.textContent = "●";
  banner.appendChild(dotL);
  banner.appendChild(text);
  banner.appendChild(dotR);
  return banner;
}

/* ------------------------------------------------------------------ */
/* Fixture loader                                                     */
/* ------------------------------------------------------------------ */

function parseJsonl(text: string): WsMessage[] {
  const out: WsMessage[] = [];
  for (const raw of text.split(/\r?\n/)) {
    const trimmed = raw.trim();
    if (!trimmed) continue;
    let parsed: unknown;
    try {
      parsed = JSON.parse(trimmed);
    } catch {
      continue;
    }
    if (isWsMessage(parsed)) out.push(parsed);
  }
  return out;
}

export function PlaybackMode(props: PlaybackModeProps = {}): JSX.Element {
  const [engaged, setEngaged] = useState<EngagedState>(
    props.initialEngaged ? "playback" : "live",
  );
  const reset = useWsStore((s) => s.reset);
  const ingest = useWsStore((s) => s.ingest);

  // The React-owned host is just a marker element. The banner DOM
  // itself is imperatively appended to document.body inside an effect
  // so external `.remove()` calls never collide with the reconciler.
  const bannerRef = useRef<HTMLDivElement | null>(null);
  const timersRef = useRef<ReturnType<typeof setTimeout>[]>([]);

  const fixtureUrl = props.fixtureUrl ?? DEFAULT_FIXTURE_URL;
  const timeScale = props.timeScale ?? 1.0;

  /* --- toggle handler ------------------------------------------------ */

  const toggle = useCallback(() => {
    setEngaged((cur) => (cur === "playback" ? "live" : "playback"));
  }, []);

  /* --- fixture playback effect -------------------------------------- */

  useEffect(() => {
    if (engaged !== "playback") return;
    if (typeof window === "undefined") return;

    let cancelled = false;

    const ingestAll = (frames: readonly WsMessage[]) => {
      reset();
      if (frames.length === 0) return;
      const anchorTs = Date.parse(frames[0]!.ts);
      for (const f of frames) {
        const dt = Math.max(0, Date.parse(f.ts) - anchorTs);
        const delay = dt * timeScale;
        if (delay <= 0) {
          if (!cancelled) ingest(f);
        } else {
          const t = setTimeout(() => {
            if (!cancelled) ingest(f);
          }, delay);
          timersRef.current.push(t);
        }
      }
    };

    if (props.fixtureFrames) {
      const valid = props.fixtureFrames.filter(isWsMessage) as WsMessage[];
      ingestAll(valid);
      return () => {
        cancelled = true;
        for (const t of timersRef.current) clearTimeout(t);
        timersRef.current = [];
      };
    }

    void (async () => {
      try {
        const res = await fetch(fixtureUrl, { cache: "no-store" });
        if (!res.ok) return;
        const body = await res.text();
        if (cancelled) return;
        const frames = parseJsonl(body);
        ingestAll(frames);
      } catch {
        /* network blip — surface nothing; banner still up */
      }
    })();

    return () => {
      cancelled = true;
      for (const t of timersRef.current) clearTimeout(t);
      timersRef.current = [];
    };
  }, [engaged, fixtureUrl, ingest, reset, timeScale, props.fixtureFrames]);

  /* --- toggle-off cleanup ------------------------------------------- */

  useEffect(() => {
    if (engaged !== "live") return;
    reset();
  }, [engaged, reset]);

  /* --- Imperative banner mount + MutationObserver anti-tamper guard - */

  useEffect(() => {
    if (engaged !== "playback") return;
    if (typeof window === "undefined" || typeof document === "undefined") return;

    const ensureBanner = (): HTMLDivElement => {
      const existing = bannerRef.current;
      if (existing && existing.isConnected) return existing;
      const fresh = createBannerNode();
      document.body.appendChild(fresh);
      bannerRef.current = fresh;
      return fresh;
    };

    ensureBanner();

    let observer: MutationObserver | null = null;
    if (typeof MutationObserver !== "undefined") {
      observer = new MutationObserver(() => {
        if (bannerNodeOK(bannerRef.current)) return;
        // Banner tampered with — re-create immediately.
        ensureBanner();
      });
      observer.observe(document.body, {
        childList: true,
        subtree: true,
        attributes: true,
        attributeFilter: ["style", "class", "hidden"],
      });
    }

    // Heartbeat — observer alone races with `el.remove()` in some jsdom
    // versions. The 250ms tick is the demo-team's tolerance budget: if
    // the banner is missing for more than a quarter-second the operator
    // probably already noticed visually.
    const heartbeat = setInterval(() => {
      if (!bannerNodeOK(bannerRef.current)) ensureBanner();
    }, 250);

    return () => {
      if (observer) observer.disconnect();
      clearInterval(heartbeat);
      const node = bannerRef.current;
      if (node && node.isConnected) {
        try {
          node.remove();
        } catch {
          /* ignore */
        }
      }
      bannerRef.current = null;
    };
  }, [engaged]);

  /* --- render -------------------------------------------------------- */

  // Only the TOGGLE button is React-managed. The banner is imperative
  // (see the effect above) so anti-tamper recovery never collides with
  // the reconciler.

  const toggleLabel = useMemo(
    () => (engaged === "playback" ? "Exit Playback" : "PLAYBACK"),
    [engaged],
  );

  return (
    <button
      type="button"
      data-testid={TOGGLE_TEST_ID}
      aria-pressed={engaged === "playback"}
      aria-label="Toggle Demo Playback Mode"
      onClick={toggle}
      className={
        "fixed right-4 top-4 z-[9998] rounded-md border border-genesis-ink-muted/60 " +
        "bg-genesis-bg/85 px-3 py-2 text-sm font-semibold tracking-wide " +
        "text-genesis-ink shadow-lg backdrop-blur " +
        (engaged === "playback"
          ? "ring-2 ring-genesis-amber"
          : "hover:border-genesis-amber/60")
      }
    >
      {toggleLabel}
    </button>
  );
}

export default PlaybackMode;

/* ------------------------------------------------------------------ */
/* Test exports — vitest spec asserts against these                   */
/* ------------------------------------------------------------------ */

export const __TEST__ = {
  BANNER_TEST_ID,
  TOGGLE_TEST_ID,
  BANNER_DATA_ATTR,
  DEFAULT_FIXTURE_URL,
  BANNER_CLASSNAMES,
  BANNER_TEXT,
  bannerNodeOK,
  parseJsonl,
  createBannerNode,
};
