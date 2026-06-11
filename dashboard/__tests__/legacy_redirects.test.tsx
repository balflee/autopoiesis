import { afterEach, describe, expect, it, vi } from "vitest";

import "./setup";

/**
 * TASK G2 — legacy dev surfaces fold into the lifeline.
 *
 * `/workshop` and `/playback` are legacy/dev routes that are NOT part of
 * the ABYSS lifeline (roadmap · backtest · survival · mock). To stop
 * stale links/bookmarks landing on an orphan navy page, each page is now
 * a server component that calls next/navigation `redirect('/roadmap')`,
 * mirroring the site root at `app/page.tsx`.
 *
 * These tests mock `next/navigation` so we can assert each default export
 * delegates to `redirect('/roadmap')`. (`app/live` is intentionally left
 * alone — it is the kept-but-unsurfaced legacy live view.)
 */
vi.mock("next/navigation", () => ({
  redirect: vi.fn((url: string) => {
    // The real redirect() throws a NEXT_REDIRECT control-flow error; we
    // mirror that so a page that does `return redirect(...)` still halts.
    throw new Error(`NEXT_REDIRECT:${url}`);
  }),
}));

import { redirect } from "next/navigation";

const redirectMock = vi.mocked(redirect);

afterEach(() => {
  redirectMock.mockClear();
});

describe("legacy route redirects → /roadmap", () => {
  it("/workshop redirects to /roadmap", async () => {
    const { default: WorkshopPage } = await import("../app/workshop/page");
    expect(() => WorkshopPage()).toThrow("NEXT_REDIRECT:/roadmap");
    expect(redirectMock).toHaveBeenCalledWith("/roadmap");
  });

  it("/playback redirects to /roadmap", async () => {
    const { default: PlaybackPage } = await import("../app/playback/page");
    expect(() => PlaybackPage()).toThrow("NEXT_REDIRECT:/roadmap");
    expect(redirectMock).toHaveBeenCalledWith("/roadmap");
  });

  it("/ (site root) redirects to /roadmap", async () => {
    // app/page.tsx is the same shape: a server component that redirects to
    // the roadmap lifeline so `/` has a single canonical home.
    const { default: RootPage } = await import("../app/page");
    expect(() => RootPage()).toThrow("NEXT_REDIRECT:/roadmap");
    expect(redirectMock).toHaveBeenCalledWith("/roadmap");
  });
});
