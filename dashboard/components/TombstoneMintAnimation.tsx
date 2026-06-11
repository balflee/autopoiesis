"use client";

/**
 * TombstoneMintAnimation — closes the Demo §9 storyboard with a quiet,
 * dignified flourish: the TombstoneNFT mint confirms, token id + IPFS
 * CID slide in, and the entire surface fades to grey.
 *
 * PRD §5.1.C requires that `ipfs_degraded === true` be surfaced
 * VISIBLY — a silent fallback to the happy-path render is a contract
 * violation. We render an amber 'memory bank pin failed — text-only
 * tombstone' badge and replace the CID slot with the same string.
 *
 * Animation: pure CSS keyframes via .genesis-tombstone-mint — keeps
 * Framer Motion out of the bundle (bundle-size budget for the
 * lighthouse_perf gate). Respects reduced motion via the
 * @media (prefers-reduced-motion: reduce) escape hatch in
 * death_watch.css.
 */

import type { JSX } from "react";

import { ColorTokens } from "@/lib/colorTokens";
import type { TombstoneEntry } from "@/lib/wsStore";

export interface TombstoneMintAnimationProps {
  readonly tombstone: TombstoneEntry;
}

const IPFS_DEGRADED_NOTE = "memory bank pin failed — text-only tombstone";

export function TombstoneMintAnimation(
  props: TombstoneMintAnimationProps,
): JSX.Element {
  const { tombstone } = props;
  const degraded = tombstone.ipfs_degraded;

  return (
    <section
      data-testid="tombstone-mint-animation"
      data-token-id={tombstone.token_id}
      data-ipfs-degraded={degraded ? "true" : "false"}
      role="status"
      aria-live="polite"
      aria-label="Tombstone minted"
      className="genesis-tombstone-mint flex w-full max-w-3xl flex-col items-center gap-3 rounded-lg border border-genesis-ink-muted/30 px-4 py-4 text-center sm:py-5"
      style={{
        backgroundColor: "rgba(159, 176, 196, 0.06)",
      }}
    >
      <span
        className="font-mono text-[11px] uppercase tracking-[0.3em]"
        style={{ color: ColorTokens.INK_MUTED }}
      >
        Tombstone Minted
      </span>

      <dl className="grid w-full grid-cols-1 gap-x-6 gap-y-2 text-left font-mono text-xs sm:grid-cols-2 sm:text-sm">
        <div className="flex flex-col">
          <dt className="text-[10px] uppercase tracking-[0.2em] text-genesis-ink-muted">
            token id
          </dt>
          <dd
            data-testid="tombstone-token-id"
            className="text-genesis-ink"
          >
            #{tombstone.token_id}
          </dd>
        </div>

        <div className="flex flex-col">
          <dt className="text-[10px] uppercase tracking-[0.2em] text-genesis-ink-muted">
            memory bank
          </dt>
          {degraded ? (
            <dd
              data-testid="tombstone-ipfs-degraded-badge"
              className="font-mono text-genesis-amber"
              style={{ color: ColorTokens.AMBER }}
            >
              {IPFS_DEGRADED_NOTE}
            </dd>
          ) : (
            <dd
              data-testid="tombstone-ipfs-cid"
              className="break-all text-genesis-ink"
            >
              {tombstone.ipfs_cid ?? "—"}
            </dd>
          )}
        </div>
      </dl>

      {tombstone.tx_hash && (
        <a
          data-testid="tombstone-tx-hash"
          href={`https://polygonscan.com/tx/${tombstone.tx_hash}`}
          target="_blank"
          rel="noopener noreferrer"
          className="font-mono text-[10px] uppercase tracking-[0.2em] text-genesis-ink-muted underline-offset-2 hover:underline"
        >
          mint tx · {tombstone.tx_hash.slice(0, 10)}…
          {tombstone.tx_hash.slice(-6)}
        </a>
      )}
    </section>
  );
}

export default TombstoneMintAnimation;
