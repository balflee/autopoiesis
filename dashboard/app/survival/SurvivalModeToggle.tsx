"use client";

/**
 * SurvivalModeToggle — the "Numerical vs AI" survival-journey switch (E-toggle).
 *
 * A clean two-option segmented control near the top of /survival. Switching
 * re-feeds {@link SurvivalJourneyView} from the selected journey:
 *   - Numerical — deterministic EMA learning, no LLM. Always available.
 *   - AI — Gemini reflection-driven. May be UNAVAILABLE (artifact not generated
 *     yet); when so, the option is shown but DISABLED with a "pending"
 *     hint so the toggle is visibly present for the demo without breaking.
 *
 * Abyssal-scoped, presentational — the active option glows electric-lime
 * (`--ab-glow`); no new accent colors. The parent owns the selected-mode state.
 */

import type { JSX } from "react";

import type { SurvivalJourneyMode } from "@/lib/load_survival_journey.server";

interface ModeMeta {
  readonly mode: SurvivalJourneyMode;
  readonly label: string;
  readonly sub: string;
}

const MODES: readonly ModeMeta[] = [
  { mode: "numerical", label: "Numerical", sub: "deterministic EMA learning" },
  { mode: "ai", label: "AI", sub: "LLM reflection · MiniMax" },
];

// Optional extra modes — only rendered when their artifact is actually present
// (absence is the normal fresh-checkout state, not an error, so no disabled
// placeholder for these): the Gemini-only provider-comparison leg + the
// archived pre-realism-rules run1 snapshots (finetune-log exhibits).
const GEMINI_MODE: ModeMeta = {
  mode: "ai_gemini", label: "AI · Gemini", sub: "Gemini-only · provider comparison",
};
const RUN1_MODES: readonly ModeMeta[] = [
  { mode: "numerical_run1", label: "Numerical · v1", sub: "archived · no realism rules" },
  { mode: "ai_run1", label: "AI · v1", sub: "archived · no realism rules" },
];

export interface SurvivalModeToggleProps {
  readonly mode: SurvivalJourneyMode;
  readonly onChange: (mode: SurvivalJourneyMode) => void;
  /** Whether the AI journey artifact is loaded + available. */
  readonly aiAvailable: boolean;
  /** Whether the Gemini-only provider-comparison artifact is available. */
  readonly aiGeminiAvailable?: boolean;
  /** Whether the archived run1 (pre-rules) artifacts are available. */
  readonly numericalRun1Available?: boolean;
  readonly aiRun1Available?: boolean;
}

export function SurvivalModeToggle({
  mode,
  onChange,
  aiAvailable,
  aiGeminiAvailable = false,
  numericalRun1Available = false,
  aiRun1Available = false,
}: SurvivalModeToggleProps): JSX.Element {
  const modes: ModeMeta[] = [...MODES];
  if (aiGeminiAvailable) modes.push(GEMINI_MODE);
  if (numericalRun1Available) modes.push(RUN1_MODES[0]!);
  if (aiRun1Available) modes.push(RUN1_MODES[1]!);
  return (
    <div
      data-testid="survival-mode-toggle"
      role="radiogroup"
      aria-label="Survival journey: numerical vs AI (current + archived v1)"
      className="inline-flex flex-wrap items-stretch gap-1 rounded-xl border border-[var(--ab-moss)]/30 bg-[var(--ab-bg-2)]/60 p-1"
    >
      {modes.map((m) => {
        const active = m.mode === mode;
        const disabled = m.mode === "ai" && !aiAvailable;
        return (
          <button
            key={m.mode}
            type="button"
            role="radio"
            data-testid={`survival-mode-${m.mode}`}
            data-active={active}
            // Single-select segmented control → expose its state as a radio.
            // `aria-checked` is the canonical state for role=radio; we also
            // keep `aria-pressed` so AT that announce toggle-buttons still read
            // the selected option (belt-and-braces, per the G4 brief).
            aria-checked={active}
            aria-pressed={active}
            aria-label={`${m.label} · ${m.sub}${disabled ? " · pending, not yet generated" : ""}`}
            disabled={disabled}
            onClick={() => {
              if (!disabled && !active) onChange(m.mode);
            }}
            className={[
              "group flex min-w-[7.5rem] flex-col gap-1 rounded-lg px-4 py-2.5 text-left transition-colors",
              "focus:outline-none focus-visible:ring-1 focus-visible:ring-[var(--ab-glow)]",
              active
                ? "bg-[var(--ab-glow-soft)] ring-1 ring-[var(--ab-glow)]/60"
                : disabled
                  ? "cursor-not-allowed opacity-50"
                  : "hover:bg-[var(--ab-moss)]/15",
            ].join(" ")}
          >
            <span className="flex items-baseline gap-2">
              <span
                className={[
                  "font-display text-lg leading-none",
                  active
                    ? "text-[var(--ab-glow)] ab-glow-text"
                    : "text-[var(--ab-text)]",
                ].join(" ")}
              >
                {m.label}
              </span>
              {disabled ? (
                <span
                  data-testid="survival-mode-ai-pending"
                  className="font-mono text-[8px] uppercase tracking-[0.2em] text-[var(--ab-death)]/80"
                >
                  pending · not yet generated
                </span>
              ) : null}
            </span>
            <span className="font-mono text-[9px] uppercase tracking-[0.18em] text-[var(--ab-dim)]">
              {m.sub}
            </span>
          </button>
        );
      })}
    </div>
  );
}

export default SurvivalModeToggle;
