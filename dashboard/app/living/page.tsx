import { type JSX } from "react";

import { LivingStageBody } from "./LivingStageBody";

export const metadata = { title: "Autopoiesis · Living Stage" };

/**
 * /living — the Living Stage showcase. A standalone abyss-skinned page (no L5
 * gate) that renders the running mock-bet agent as a living organism placing
 * real Polymarket bets inside the divine economy.
 */
export default function LivingPage(): JSX.Element {
  return (
    <main
      className="abyss min-h-screen px-6 py-8"
      data-testid="living-route"
      style={{ background: "var(--ab-bg)" }}
    >
      <LivingStageBody />
    </main>
  );
}
