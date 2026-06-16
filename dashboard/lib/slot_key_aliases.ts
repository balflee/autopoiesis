/**
 * Backward-compat slot-key aliases for legacy on-disk journey artifacts.
 *
 * The 2026-06-16 rename changed three fusion-slot keys to match the Sackmann
 * payloads they actually carry (smart_money→surface_advantage,
 * sentiment_llm→head_to_head, crowd_volume→rest_recency). Several on-disk
 * journeys (`survival_journey*.json` — verbatim finetune-log exhibits, Gemini-
 * gated runs) carry the OLD keys and are NOT regenerable, so the survival
 * loader normalizes legacy per-step signal keys before strict validation.
 *
 * Mirrors `agent/engines/slot_aliases.py`.
 */
export const SLOT_KEY_ALIASES: Record<string, string> = {
  smart_money: "surface_advantage",
  sentiment_llm: "head_to_head",
  crowd_volume: "rest_recency",
};

/**
 * Normalize legacy slot keys in a per-step signals object (old→new); identity
 * for already-renamed keys. Lets archived/old-key journeys validate post-rename.
 */
export function normalizeSlotKeys(
  o: Record<string, unknown>,
): Record<string, unknown> {
  const out: Record<string, unknown> = { ...o };
  for (const [oldK, newK] of Object.entries(SLOT_KEY_ALIASES)) {
    if (oldK in out && !(newK in out)) {
      out[newK] = out[oldK];
      delete out[oldK];
    }
  }
  return out;
}
