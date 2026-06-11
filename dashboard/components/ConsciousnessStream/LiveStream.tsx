"use client";

/**
 * LiveStream — typewriter rendering of WS `thought` frames.
 *
 * Sprint_3 (T-D-002) scope: real-time thoughts from the agent backend
 * are surfaced here while the PLAYBACK takeover is dormant. The newest
 * thought animates one character at a time (PRD §8 typewriter effect);
 * older thoughts dim into a tail behind it.
 *
 * Sources state ONLY from `useWsStore` — no fetch, no localStorage, no
 * direct WS access. Tests inject mock thoughts via `useWsStore.getState().ingest`.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import type { JSX } from "react";

import { widgetPalette, type WidgetVariant } from "@/lib/colorTokens";
import { useWsStore, type ThoughtEntry } from "@/lib/wsStore";

interface LiveStreamProps {
  /** Override the inter-character delay for tests / accessibility. */
  readonly charIntervalMs?: number;
  /** Surface the "back to playback" affordance? Default true. */
  readonly showPlaybackHint?: boolean;
  /** Theme variant — `"navy"` (default, legacy) or `"abyss"` (/mock). */
  readonly variant?: WidgetVariant;
}

const DEFAULT_CHAR_MS = 24;

export function LiveStream(props: LiveStreamProps = {}): JSX.Element {
  const thoughts = useWsStore((s) => s.thoughts);
  const connection = useWsStore((s) => s.connection);
  const llmActivated = useWsStore((s) => s.llmActivated);
  const variant = props.variant ?? "navy";
  const pal = widgetPalette(variant);

  const newest = thoughts[thoughts.length - 1] ?? null;
  const tail = useMemo(() => thoughts.slice(0, -1), [thoughts]);

  return (
    <section
      data-testid="consciousness-live-stub"
      role="region"
      aria-label="Consciousness stream — live"
      className={`flex h-full min-h-[60vh] w-full flex-col gap-6 rounded-lg border p-6 ${pal.panelSolid}`}
    >
      <header
        className={`flex items-center justify-between font-mono text-xs uppercase tracking-[0.2em] ${pal.textMuted}`}
      >
        <span>live · consciousness · {connection}</span>
        {llmActivated ? (
          <span
            data-testid="consciousness-llm-flag"
            style={{ color: pal.accent2 }}
          >
            llm engaged
          </span>
        ) : null}
      </header>

      <TailParagraphs entries={tail} variant={variant} />

      <NewestParagraph
        entry={newest}
        charIntervalMs={props.charIntervalMs ?? DEFAULT_CHAR_MS}
        variant={variant}
      />

      {props.showPlaybackHint !== false ? (
        <footer
          className={`mt-auto font-mono text-xs uppercase tracking-[0.2em] ${pal.textMuted}`}
        >
          press P to enter playback · esc to return to live
        </footer>
      ) : null}
    </section>
  );
}

function TailParagraphs(props: {
  entries: readonly ThoughtEntry[];
  variant: WidgetVariant;
}): JSX.Element | null {
  if (props.entries.length === 0) return null;
  return (
    <div
      data-testid="consciousness-tail"
      className={`flex flex-col gap-2 text-base ${widgetPalette(props.variant).textMuted}`}
    >
      {props.entries.map((e) => (
        <p key={e.seq} data-testid={`consciousness-tail-${e.seq}`}>
          {e.text}
        </p>
      ))}
    </div>
  );
}

function NewestParagraph(props: {
  entry: ThoughtEntry | null;
  charIntervalMs: number;
  variant: WidgetVariant;
}): JSX.Element {
  const [shown, setShown] = useState<string>("");
  const lastSeqRef = useRef<number>(-1);

  useEffect(() => {
    if (!props.entry) {
      setShown("");
      lastSeqRef.current = -1;
      return;
    }
    if (lastSeqRef.current === props.entry.seq) {
      // Same thought, don't restart.
      return;
    }
    lastSeqRef.current = props.entry.seq;
    setShown("");

    if (props.charIntervalMs <= 0) {
      setShown(props.entry.text);
      return;
    }

    let i = 0;
    const text = props.entry.text;
    const id = setInterval(() => {
      i += 1;
      if (i >= text.length) {
        setShown(text);
        clearInterval(id);
      } else {
        setShown(text.slice(0, i));
      }
    }, props.charIntervalMs);
    return () => clearInterval(id);
  }, [props.entry, props.charIntervalMs]);

  if (!props.entry) {
    return (
      <p
        data-testid="consciousness-empty"
        className={`text-base ${widgetPalette(props.variant).textMuted}`}
      >
        awaiting first thought from the agent…
      </p>
    );
  }

  return (
    <p
      data-testid="consciousness-newest"
      data-seq={props.entry.seq}
      className={`text-diary-base font-sans ${widgetPalette(props.variant).textStrong}`}
    >
      {shown}
      <Caret variant={props.variant} />
    </p>
  );
}

function Caret({ variant }: { variant: WidgetVariant }): JSX.Element {
  return (
    <span
      aria-hidden
      className="ml-1 inline-block w-[0.5ch] animate-pulse"
      style={{ color: widgetPalette(variant).accent2 }}
    >
      ▍
    </span>
  );
}

export default LiveStream;
