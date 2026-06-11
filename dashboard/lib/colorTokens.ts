/**
 * Genesis color tokens — PRD §8 lines 484-523.
 *
 * These four hex codes are the ONLY chromatic identity for the demo. Other
 * colors (greys, whites) derive from system text contrast against {@link BG}.
 * Do NOT invent new tokens here without a registry-bumping spec change.
 *
 * Contrast budget vs BG (#0B1426):
 *   - INK (#F5F7FA)   ≈ 16.0:1   (AAA)
 *   - LOSS (#E63946)  ≈  4.4:1   (AA Large only — restrict to ≥24px)
 *   - WIN  (#06D6A0)  ≈  6.2:1   (AA / AAA Large)
 *   - AMBER(#FFB703)  ≈  9.6:1   (AAA)
 */
export const ColorTokens = {
  BG: "#0B1426",
  LOSS: "#E63946",
  WIN: "#06D6A0",
  AMBER: "#FFB703",
  INK: "#F5F7FA",
  INK_MUTED: "#9FB0C4",
} as const;

export type ColorTokenName = keyof typeof ColorTokens;

/**
 * Abyssal-bioluminescent chart palette — the hex literals the `.abyss` design
 * system's `--ab-*` CSS variables (app/globals.css) resolve to. The shared
 * chart primitives (PnLBaselineChart / WeightEvolutionChart / BacktestScrubber)
 * are theme-able via a `variant` prop; the SURVIVAL page passes `"abyss"` so the
 * charts render lime-on-near-black, cohesive with the rest of /survival, while
 * the default `"navy"` variant keeps the legacy Phase-1 look byte-for-byte.
 *
 * Why literal hex (not `var(--ab-glow)`): the SVG `stroke`/`fill` ATTRIBUTES
 * and the inline-style legend swatch must resolve to the SAME concrete color so
 * the line↔legend visual binding holds (and stays assertable). jsdom does not
 * resolve `var()`, and an SVG presentation attribute set to `var(--x)` is not
 * universally honored; a concrete hex is unambiguous in both the browser and
 * the test DOM. These values MUST stay in lock-step with the `--ab-*` block in
 * globals.css.
 */
export const AbyssColors = {
  BG: "#060d0b", // --ab-bg — near-black abyssal floor
  BG_2: "#0a1714", // --ab-bg-2 — raised panel core
  GLOW: "#c8f94c", // --ab-glow — electric-lime bioluminescent accent
  MOSS: "#2e5e50", // --ab-moss — secondary teal structure
  TEXT: "#dbe7e0", // --ab-text — body text
  DIM: "#9fb3a9", // --ab-dim — secondary labels / axes / grid
  DEATH: "#ff6b4a", // --ab-death — permadeath warning red
} as const;

/** The two chart color themes the shared primitives support. */
export type ChartVariant = "navy" | "abyss";

/**
 * Widget theme variant — the SAME two-theme contract as {@link ChartVariant},
 * but for the LIVE telemetry widgets (VitalsPanel / DualEngineMeter / LiveStream
 * / DecisionFeed / DeathWatch) that the /mock (Page 3) abyss surface reuses.
 *
 * Those widgets carry TWO kinds of color: (a) inline `style={{ color:
 * ColorTokens.* }}` chromatic accents that a `.abyss` CSS wrapper can NOT
 * recolor, and (b) structural `genesis-*` Tailwind classes (border / bg / text).
 * {@link widgetPalette} resolves BOTH per variant so a single additive
 * `variant` prop re-skins a whole widget. Default is `"navy"` everywhere, so
 * /live stays byte-for-byte unchanged.
 */
export type WidgetVariant = "navy" | "abyss";

/** Semantic, theme-resolved widget palette (see {@link widgetPalette}). */
export interface WidgetPalette {
  /* Inline chromatic accents (was `ColorTokens.*`). */
  /** Primary / positive accent (was WIN). */
  readonly accent: string;
  /** Secondary / "live"/warning accent (was AMBER). */
  readonly accent2: string;
  /** Loss / danger / death accent (was LOSS). */
  readonly danger: string;
  /** Strong body text (was INK). */
  readonly ink: string;
  /** Muted / secondary text (was INK_MUTED). */
  readonly inkMuted: string;
  /* Structural Tailwind class fragments. */
  /** Panel container classes: border + bg + text. */
  readonly panel: string;
  /** Faint-border inner panel: the /20 border + translucent bg, NO text
   *  color (the legacy DecisionFeed row-detail inherited its text). */
  readonly panelFaint: string;
  /** Loaded-state panel container (opaque bg variant). */
  readonly panelSolid: string;
  /** Muted-text class. */
  readonly textMuted: string;
  /** Strong-text class. */
  readonly textStrong: string;
  /** Hairline border class. */
  readonly border: string;
  /** Fainter hairline border class (the /20-opacity tone). */
  readonly borderFaint: string;
  /** Faint fill / track bg class. */
  readonly track: string;
  /** Translucent hover bg class. */
  readonly hover: string;
}

const NAVY_WIDGET_PALETTE: WidgetPalette = {
  accent: ColorTokens.WIN,
  accent2: ColorTokens.AMBER,
  danger: ColorTokens.LOSS,
  ink: ColorTokens.INK,
  inkMuted: ColorTokens.INK_MUTED,
  panel: "border-genesis-ink-muted/30 bg-genesis-bg/60 text-genesis-ink-muted",
  panelFaint: "border-genesis-ink-muted/20 bg-genesis-bg/60",
  panelSolid: "border-genesis-ink-muted/30 bg-genesis-bg text-genesis-ink",
  textMuted: "text-genesis-ink-muted",
  textStrong: "text-genesis-ink",
  border: "border-genesis-ink-muted/30",
  borderFaint: "border-genesis-ink-muted/20",
  track: "bg-genesis-ink-muted/20",
  hover: "hover:bg-genesis-ink-muted/5",
};

const ABYSS_WIDGET_PALETTE: WidgetPalette = {
  accent: AbyssColors.GLOW,
  accent2: AbyssColors.GLOW,
  danger: AbyssColors.DEATH,
  ink: AbyssColors.TEXT,
  inkMuted: AbyssColors.DIM,
  panel:
    "border-[var(--ab-moss)]/30 bg-[var(--ab-bg-2)]/60 text-[var(--ab-dim)]",
  panelFaint: "border-[var(--ab-moss)]/20 bg-[var(--ab-bg-2)]/60",
  panelSolid:
    "border-[var(--ab-moss)]/30 bg-[var(--ab-bg-2)]/60 text-[var(--ab-text)]",
  textMuted: "text-[var(--ab-dim)]",
  textStrong: "text-[var(--ab-text)]",
  border: "border-[var(--ab-moss)]/30",
  borderFaint: "border-[var(--ab-moss)]/20",
  track: "bg-[var(--ab-bg-3)]",
  hover: "hover:bg-[var(--ab-glow-soft)]",
};

/**
 * Resolve the semantic widget palette for a theme `variant`. `"navy"`
 * (the default) reproduces the legacy Phase-1 look exactly; `"abyss"` maps
 * onto the bioluminescent lime-on-near-black `--ab-*` tokens.
 */
export function widgetPalette(variant: WidgetVariant = "navy"): WidgetPalette {
  return variant === "abyss" ? ABYSS_WIDGET_PALETTE : NAVY_WIDGET_PALETTE;
}

/**
 * Phase-to-color mapping used by {@link PlaybackTakeover} when it tints
 * a tick header / accent strip. `lead_in` and `reflection` deliberately
 * stay neutral; the climax / outcome carry the chromatic load so the
 * narrative beat lands hard for the demo audience.
 */
export const TICK_PHASE_ACCENT: Record<
  "lead_in" | "climax" | "outcome" | "reflection",
  string
> = {
  lead_in: ColorTokens.INK_MUTED,
  climax: ColorTokens.AMBER,
  outcome: ColorTokens.LOSS, // overridden per-tick if result === 'WIN'
  reflection: ColorTokens.INK,
};
