import type { Config } from "tailwindcss";

/**
 * Genesis Dashboard — Tailwind theme.
 *
 * Color tokens are mirrored from `dashboard/lib/colorTokens.ts`
 * (PRD §8 lines 484-523). Do NOT add invented colors — the demo
 * requires brand consistency.
 */
const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // PRD §8 tokens — exact hex
        "genesis-bg": "#0B1426",
        "genesis-loss": "#E63946",
        "genesis-win": "#06D6A0",
        "genesis-amber": "#FFB703",
        "genesis-ink": "#F5F7FA", // ≥AAA contrast vs #0B1426 (~16:1)
        "genesis-ink-muted": "#9FB0C4",
      },
      fontSize: {
        // Projector readability: diary copy must be ≥28px (PRD §8).
        "diary-base": ["28px", { lineHeight: "38px", letterSpacing: "0.005em" }],
        "diary-emphasis": ["32px", { lineHeight: "44px", letterSpacing: "0.005em" }],
      },
      fontFamily: {
        // System stacks — keeps install footprint tiny; no webfont fetch.
        // PRD §8 names Source Serif Pro / JetBrains Mono / Inter as the
        // display families; we PREFER them and FALL BACK to system stacks
        // so the demo machine can install the brand fonts without code
        // changes, but unbranded environments still render legibly.
        sans: [
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "Helvetica",
          "Arial",
          "sans-serif",
        ],
        mono: [
          "JetBrains Mono",
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Monaco",
          "Consolas",
          "monospace",
        ],
        // Display serif for PLAYBACK narrative paragraphs (PRD §8 28-32px).
        "serif-display": [
          "Source Serif Pro",
          "Source Serif 4",
          "ui-serif",
          "Georgia",
          "Cambria",
          "Times New Roman",
          "Times",
          "serif",
        ],
      },
    },
  },
  plugins: [],
};

export default config;
