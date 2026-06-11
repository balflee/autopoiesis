/**
 * StageShell.tsx — the shared ABYSS lifeline shell (G1).
 *
 * Every non-hub abyss page (/backtest, /survival, /mock) used to copy-paste the
 * exact same wrapper: a `<main className="abyss">` → centered `max-w-5xl`
 * container → a hero `<header>` carrying a top-left "◂ lifeline" back-link to
 * /roadmap, a top-right stage label, an eyebrow, a serif title, and an italic
 * glow subtitle — then the page body, then a `border-t` footer with prev/next
 * lifeline cross-links.
 *
 * This component renders that shell ONCE, parameterized by the per-page
 * metadata in {@link import("../../lib/lifeline").StageMeta}. Its rendered DOM
 * is byte-identical to the markup each page previously inlined: same element
 * tree, same Tailwind classes, same data-testids, same text. It is a pure DRY
 * refactor — NOT a redesign.
 *
 * No "use client": StageShell uses only `next/link` + pure JSX, so it is safe
 * to import from BOTH server components (/survival, /mock) and the one client
 * component (/backtest). The footer varies structurally per page (single next
 * link vs. a back+gated-next pair vs. a glow back-link), so it is supplied by
 * the page as the `footer` prop and wrapped by the shared
 * {@link LifelineFooter} for the byte-identical `<footer>` chrome.
 */

import Link from "next/link";
import type { CSSProperties, JSX, ReactNode } from "react";

import type { StageMeta } from "@/lib/lifeline";

export interface StageShellProps {
  /** the per-stage chrome metadata (back-link testid, labels, title, …) */
  meta: StageMeta;
  /** the page body — rendered between the hero header and the footer */
  children: ReactNode;
  /**
   * extra hero content rendered as the LAST child INSIDE `<header>` (e.g.
   * /backtest's headline-telemetry strip, which lived inside the hero header).
   * Omitted by /survival + /mock, whose headers carry no trailing block.
   */
  heroExtra?: ReactNode;
  /**
   * the page footer node — typically a {@link LifelineFooter}. Optional so the
   * locked/empty-state variants (which intentionally render no footer) can omit
   * it and stay byte-identical to their previous inline markup.
   */
  footer?: ReactNode;
}

/**
 * Build the inline `animation-delay` style for a hero text element, or
 * `undefined` (→ NO style attribute) when the stage carries no delay. Keeps
 * /survival + /mock byte-identical (they had no style) while reproducing
 * /backtest's 60/120/240ms stagger.
 */
function delayStyle(ms: number | undefined): CSSProperties | undefined {
  return ms === undefined ? undefined : { animationDelay: `${ms}ms` };
}

/**
 * The shared lifeline shell: `<main className="abyss">` → container → hero
 * header (back-link + stage label + eyebrow + title + subtitle [+ heroExtra])
 * → body → footer.
 */
export function StageShell({
  meta,
  children,
  heroExtra,
  footer,
}: StageShellProps): JSX.Element {
  const d = meta.heroDelaysMs ?? {};
  return (
    <main id="main-content" className="abyss" data-testid={meta.testId}>
      <div className="mx-auto flex w-full max-w-5xl flex-col px-5 pb-24 pt-14 sm:px-8 sm:pt-20">
        <header className={meta.headerMarginClass}>
          {/* G3: wrap the back-link + stage label so the two mono labels stack
              gracefully on narrow screens (≤375px) instead of colliding; the
              desktop row is unchanged (it never wraps at md+ widths). */}
          <div className="mb-8 flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
            <Link
              href="/roadmap"
              data-testid={meta.backLinkTestId}
              aria-label="Back to the lifeline overview"
              className="rounded-sm font-mono text-[10px] uppercase tracking-[0.28em] text-[var(--ab-dim)] transition-colors hover:text-[var(--ab-glow)] focus:outline-none focus-visible:text-[var(--ab-glow)] focus-visible:ring-2 focus-visible:ring-[var(--ab-glow)]/70"
            >
              ◂ lifeline
            </Link>
            <span className="font-mono text-[10px] uppercase tracking-[0.28em] text-[var(--ab-dim)]">
              {meta.stageLabel}
            </span>
          </div>

          <p
            className="ab-hero-in mb-4 font-mono text-[10px] uppercase tracking-[0.5em] text-[var(--ab-dim)]"
            style={delayStyle(d.eyebrow)}
          >
            {meta.eyebrow}
          </p>
          <h1
            className="ab-hero-in font-display text-6xl leading-[0.9] text-[var(--ab-text)] sm:text-7xl"
            style={delayStyle(d.title)}
          >
            {meta.title}
          </h1>
          <p
            className="ab-hero-in mt-4 max-w-2xl font-display text-xl italic text-[var(--ab-glow)] ab-glow-text sm:text-2xl"
            style={delayStyle(d.subtitle)}
          >
            {meta.subtitle}
          </p>
          {heroExtra}
        </header>
        {children}
        {footer}
      </div>
    </main>
  );
}

export interface LifelineFooterProps {
  /** the left-aligned provenance note span (page-specific text) */
  note: ReactNode;
  /** the right-aligned navigation region (prev/next cross-links) */
  nav: ReactNode;
}

/**
 * The shared `<footer>` chrome every non-hub page renders byte-identically: the
 * `border-t` divider, the `justify-between` flex row, the left provenance
 * `<span>`, and a right-aligned navigation region. The right region differs in
 * STRUCTURE per page (one next link vs. a back+gated-next pair vs. a glow
 * back-link), so it is passed as `nav`. The link styling helpers below build
 * the byte-identical anchors from the lifeline metadata.
 */
export function LifelineFooter({ note, nav }: LifelineFooterProps): JSX.Element {
  return (
    <footer className="mt-20 flex flex-wrap items-baseline justify-between gap-3 border-t border-[var(--ab-moss)]/25 pt-6 font-mono text-[10px] uppercase tracking-[0.28em] text-[var(--ab-dim)]">
      <span>{note}</span>
      {nav}
    </footer>
  );
}

/**
 * A forward "next ·" cross-link in the glow accent (e.g. "next · learning to
 * survive ▸"). Byte-identical to the inline next-link anchors the pages used.
 */
export function NextLink({
  href,
  children,
  testId,
  ariaLabel,
}: {
  href: string;
  children: ReactNode;
  testId?: string;
  /** Discernible name for AT (the visible text carries ▸/◂ glyphs). */
  ariaLabel?: string;
}): JSX.Element {
  return (
    <Link
      href={href}
      data-testid={testId}
      aria-label={ariaLabel}
      className="rounded-sm text-[var(--ab-glow)]/80 transition-colors hover:text-[var(--ab-glow)] focus:outline-none focus-visible:text-[var(--ab-glow)] focus-visible:ring-2 focus-visible:ring-[var(--ab-glow)]/70"
    >
      {children}
    </Link>
  );
}

/**
 * A backward "◂" cross-link in the DIM accent (e.g. survival's "◂ back to the
 * seed"). Byte-identical to the inline dim back-link the survival footer used.
 */
export function BackLinkDim({
  href,
  children,
  testId,
  ariaLabel,
}: {
  href: string;
  children: ReactNode;
  testId?: string;
  /** Discernible name for AT (the visible text carries ▸/◂ glyphs). */
  ariaLabel?: string;
}): JSX.Element {
  return (
    <Link
      href={href}
      data-testid={testId}
      aria-label={ariaLabel}
      className="rounded-sm text-[var(--ab-dim)] transition-colors hover:text-[var(--ab-glow)] focus:outline-none focus-visible:text-[var(--ab-glow)] focus-visible:ring-2 focus-visible:ring-[var(--ab-glow)]/70"
    >
      {children}
    </Link>
  );
}
