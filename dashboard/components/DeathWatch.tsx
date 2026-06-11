"use client";

/**
 * DeathWatch — full-screen takeover that owns the Demo §9 4:00-5:00 climax.
 *
 * Trigger semantics (selectDeathWatchVisible in wsStore):
 *   - energy_threshold_crossed with direction='below' + threshold_pct=10
 *     (the producer's authoritative signal), OR
 *   - vitals.breath ≤ 10 (defensive fallback if the threshold event was
 *     dropped on a polling fallback), OR
 *   - terminalLucidityEntered latch is true (PRD §6.10 sticky — once set,
 *     the surface remains even if vitals recover).
 *
 * Visual contract:
 *   - Red palette (genesis-loss) saturating the viewport; the rest of the
 *     dashboard sits behind a translucent black scrim.
 *   - The 10%-and-below energy bar pulses (Tailwind animate-pulse, CSS
 *     fallback in death_watch.css for reduced motion).
 *   - LastWordsTypewriter mounts inside this surface once a
 *     `last_words_emitted` frame lands.
 *   - TombstoneMintAnimation mounts AFTER the last words finish typing
 *     AND a `tombstone_minted` frame is in the store.
 *
 * CLS protection (lighthouse_perf gate <0.1): the surface is `position:
 * fixed` so it does NOT push any other element when it mounts. Its inner
 * grid uses fixed pixel slots for the BREATH bar so the typewriter
 * growing letter-by-letter does not relayout the headline.
 *
 * SSR-safe: no window access inside render; mounted as a client component.
 */

import { useEffect, useMemo, type JSX } from "react";

import { ColorTokens, widgetPalette, type WidgetVariant } from "@/lib/colorTokens";
import {
  selectDeathWatchVisible,
  selectLastWordsEntry,
  selectTerminalLucidityEntered,
  selectTombstone,
  useWsStore,
  type EnergyThresholdCrossing,
} from "@/lib/wsStore";

import { LastWordsTypewriter } from "./LastWordsTypewriter";
import { TombstoneMintAnimation } from "./TombstoneMintAnimation";

const HEADLINE = "Death Watch";
const SUBLINE = "Energy reserves critical";

/**
 * The percentage rendered in the bar. Prefer the threshold-crossing
 * payload (authoritative producer signal); fall back to vitals.breath.
 */
function deriveEnergyPct(
  crossing: EnergyThresholdCrossing | null,
  breath: number | null,
): number {
  if (crossing && crossing.direction === "below") return crossing.energy_pct;
  if (breath != null) return Math.max(0, Math.min(100, breath));
  return 0;
}

export function DeathWatch({
  variant = "navy",
}: {
  /** Theme variant — `"navy"` (default, legacy) or `"abyss"` (/mock). On
   *  `"abyss"` the warning red reconciles to ab-death (#ff6b4a) so the
   *  takeover does not clash with the lime-on-near-black surface. */
  readonly variant?: WidgetVariant;
} = {}): JSX.Element | null {
  const visible = useWsStore(selectDeathWatchVisible);
  const terminalEntered = useWsStore(selectTerminalLucidityEntered);
  const breath = useWsStore((s) => s.vitals?.breath ?? null);
  const crossing = useWsStore((s) => s.energyThresholdCrossing);
  const lastWords = useWsStore(selectLastWordsEntry);
  const tombstone = useWsStore(selectTombstone);
  const causeOfDeath = useWsStore((s) => s.causeOfDeath);
  const isAbyss = variant === "abyss";
  // The chromatic RED + the whole dark scrim/structure reconcile per variant.
  // On `"abyss"` the takeover must read on the bioluminescent near-black floor
  // (the permadeath CLIMAX of /mock), not leak the navy floor (#0B1426).
  const red = isAbyss ? widgetPalette("abyss").danger : ColorTokens.LOSS;
  // Dark floor behind the scrim + gradient outer stop: navy #0B1426 vs the
  // abyss floor #060d0b (== --ab-bg). Both at 0.92 alpha (translucent scrim).
  const scrimRgba = isAbyss ? "rgba(6, 13, 11, 0.92)" : "rgba(11, 20, 38, 0.92)";
  // The red glow heart of the radial gradient stays the same warning tint; only
  // the OUTER stop follows the floor so the vignette reads on-theme.
  const gradientImage = `radial-gradient(circle at 50% 30%, rgba(230,57,70,0.35) 0%, ${
    isAbyss ? "rgba(6,13,11,0.92)" : "rgba(11,20,38,0.92)"
  } 65%)`;
  // Structural text/track classes — navy genesis-* vs the abyss --ab-* tokens.
  const subTextClass = isAbyss ? "text-[var(--ab-text)]" : "text-genesis-ink";
  const labelMutedClass = isAbyss ? "text-[var(--ab-dim)]" : "text-genesis-ink-muted";
  const valueStrongClass = isAbyss ? "text-[var(--ab-text)]" : "text-genesis-ink";
  const barTrackClass = isAbyss
    ? "bg-[var(--ab-moss-2)]"
    : "bg-genesis-ink-muted/20";

  // Body class for the dashboard background tint while Death Watch is up.
  useEffect(() => {
    if (typeof document === "undefined") return;
    if (visible) document.body.classList.add("genesis-death-watch-active");
    return () => document.body.classList.remove("genesis-death-watch-active");
  }, [visible]);

  const energyPct = useMemo(
    () => deriveEnergyPct(crossing, breath),
    [crossing, breath],
  );

  if (!visible) {
    // Render a hidden DOM hook so tests + the playwright smoke can assert
    // that the component mounted and that the visibility selector said no.
    return (
      <div
        data-testid="death-watch-root"
        data-visible="false"
        data-terminal-entered={terminalEntered ? "true" : "false"}
        className="hidden"
        aria-hidden="true"
      />
    );
  }

  // After Terminal Lucidity latches, we strip any "dismiss" affordances —
  // there is no escape. Pre-latch, we surface the trigger context so the
  // demo audience can see the runway.
  const heading = terminalEntered
    ? "Terminal Lucidity"
    : causeOfDeath
      ? "Final Breath"
      : HEADLINE;
  const subline = terminalEntered
    ? "The Agent's last hour begins."
    : SUBLINE;

  // Sticky badge surfaces when Terminal flag is set so the audience
  // understands the surface cannot be dismissed (PRD §6.10).
  return (
    <div
      data-testid="death-watch-root"
      data-visible="true"
      data-terminal-entered={terminalEntered ? "true" : "false"}
      data-energy-pct={energyPct.toFixed(1)}
      role="alert"
      aria-live="assertive"
      aria-label="Death Watch — Agent energy critical"
      className="fixed inset-0 z-[70] flex flex-col items-stretch genesis-death-watch-surface"
    >
      {/* Scrim — sits behind the chrome so the rest of the dashboard is
          still faintly visible. Tailwind doesn't have a 92% black so we
          use inline rgba. */}
      <div
        aria-hidden
        className="absolute inset-0"
        style={{
          backgroundColor: scrimRgba,
          backgroundImage: gradientImage,
        }}
      />

      <div className="relative flex h-full w-full flex-col items-center justify-start gap-6 overflow-y-auto px-4 py-8 sm:px-8 sm:py-12">
        <header
          data-testid="death-watch-header"
          className="flex w-full max-w-3xl flex-col items-center gap-2 text-center"
        >
          <span
            aria-hidden
            className="inline-block h-1 w-12 rounded-full"
            style={{ backgroundColor: red }}
          />
          <h1
            data-testid="death-watch-headline"
            className="font-mono text-3xl uppercase tracking-[0.18em] sm:text-4xl"
            style={{ color: red }}
          >
            {heading}
          </h1>
          <p
            data-testid="death-watch-subline"
            className={`font-mono text-sm uppercase tracking-[0.3em] ${subTextClass}`}
          >
            {subline}
          </p>
          {terminalEntered && (
            <span
              data-testid="death-watch-sticky-badge"
              className="mt-1 rounded border px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.2em]"
              style={{ borderColor: red, color: red }}
            >
              Terminal · cannot dismiss
            </span>
          )}
        </header>

        {/* CountdownWidget mounts in DeathWatchBorder (the gentler
            warning surface that always engages before the takeover);
            T-D-007 routes the single instance through there so getByTestId
            stays unambiguous across both surfaces. */}

        {/* Energy bar — pulses red, fixed height so the typewriter below
            never reflows it. */}
        <div
          data-testid="death-watch-energy-bar"
          className="flex w-full max-w-3xl flex-col gap-2"
        >
          <div
            className={`flex items-baseline justify-between font-mono text-xs uppercase tracking-[0.2em] ${labelMutedClass}`}
          >
            <span>BREATH</span>
            <span
              data-testid="death-watch-energy-value"
              className={valueStrongClass}
            >
              {energyPct.toFixed(0)} / 100
            </span>
          </div>
          <div
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(energyPct)}
            aria-label="Remaining BREATH"
            className={`h-4 w-full overflow-hidden rounded-full ${barTrackClass}`}
          >
            <div
              data-testid="death-watch-energy-fill"
              className="genesis-death-watch-fill h-full rounded-full"
              style={{
                width: `${Math.max(0, Math.min(100, energyPct))}%`,
                backgroundColor: red,
              }}
            />
          </div>
        </div>

        {/* Last Words typewriter — only mounts once we have text. */}
        {lastWords && (
          <LastWordsTypewriter
            text={lastWords.text}
            txHash={lastWords.tx_hash}
          />
        )}

        {/* Tombstone mint flourish — only after a confirmed mint. */}
        {tombstone && <TombstoneMintAnimation tombstone={tombstone} />}
      </div>
    </div>
  );
}

export default DeathWatch;
