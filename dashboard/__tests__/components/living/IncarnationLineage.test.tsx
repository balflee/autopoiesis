/**
 * IncarnationLineage.test.tsx — Living Stage Zone Z5 acceptance.
 *
 * Lives under dashboard/__tests__/components/living/ so vitest discovers it
 * and @testing-library/react resolves through dashboard/node_modules (see
 * vitest.config.ts include globs). Imports use the `@` alias (= dashboard
 * root). State is injected via the useWsStore Zustand seam — no network mock.
 */

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import "../../setup";

import { IncarnationLineage } from "@/components/living/IncarnationLineage";
import { useWsStore } from "@/lib/wsStore";

describe("IncarnationLineage — past lives + current living one", () => {
  beforeEach(() => {
    useWsStore.getState().reset();
  });

  it("renders a past life headstone and the current ALIVE incarnation", () => {
    useWsStore.setState({
      incarnationNumber: 1,
      reincarnationLineage: [
        {
          incarnation_number: 0,
          last_tick: 50,
          cause: "breath_zero",
          final_bankroll_usd: 0,
          ts: "t",
        },
      ],
    } as never);

    render(<IncarnationLineage />);

    // Past life 0 is present (and its cause is humanised: breath zero).
    expect(screen.getByText(/life 0/i)).toBeInTheDocument();
    expect(screen.getByTestId("incarnation-lineage").textContent).toMatch(
      /breath zero/i,
    );

    // The current life is rendered as the ALIVE marker.
    const currentEl = screen.getByTestId("lineage-current");
    expect(currentEl.textContent).toMatch(/life 1.*ALIVE/i);
  });

  it("renders only the current life when the lineage is empty", () => {
    useWsStore.setState({
      incarnationNumber: 0,
      reincarnationLineage: [],
    } as never);

    render(<IncarnationLineage />);

    const currentEl = screen.getByTestId("lineage-current");
    expect(currentEl.textContent).toMatch(/life 0.*ALIVE/i);
    // No headstone glyph when there are no past lives.
    expect(screen.getByTestId("incarnation-lineage").textContent).not.toContain(
      "✝",
    );
  });
});
