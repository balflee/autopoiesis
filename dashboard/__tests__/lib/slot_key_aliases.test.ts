/**
 * slot_key_aliases.test.ts — pure unit gate for the legacy slot-key shim.
 *
 * Mirrors agent/engines/slot_aliases.py + tests/agent/engines/test_slot_aliases.py.
 * Uses EXPLICIT old-key literals so it pins the old→new map directly.
 */

import { describe, expect, it } from "vitest";

import { SLOT_KEY_ALIASES, normalizeSlotKeys } from "@/lib/slot_key_aliases";

describe("slot_key_aliases", () => {
  it("maps exactly the three renamed slots", () => {
    expect(SLOT_KEY_ALIASES).toEqual({
      smart_money: "surface_advantage",
      sentiment_llm: "head_to_head",
      crowd_volume: "rest_recency",
    });
  });

  it("normalizeSlotKeys upgrades legacy keys (value preserved)", () => {
    const out = normalizeSlotKeys({
      tennis_technical: 0.5,
      market_momentum: 0.1,
      smart_money: -0.2,
      sentiment_llm: 1,
      crowd_volume: 0,
    });
    expect(out).toEqual({
      tennis_technical: 0.5,
      market_momentum: 0.1,
      surface_advantage: -0.2,
      head_to_head: 1,
      rest_recency: 0,
    });
  });

  it("is the identity for already-new keys", () => {
    const already = {
      surface_advantage: 0.7,
      head_to_head: 0.4,
      rest_recency: 0.2,
    };
    expect(normalizeSlotKeys(already)).toEqual(already);
  });

  it("does not clobber a new key that already coexists with its old alias", () => {
    // If both keys are present (shouldn't happen in practice), keep the new one.
    const out = normalizeSlotKeys({ smart_money: 1, surface_advantage: 2 });
    expect(out.surface_advantage).toBe(2);
  });
});
