"""V2 boot loader — re-hydrates a new agent from an ancestor's MemoryBank.

Per PRD §13 (and PRD §5.1 sub-bullet C), when the V1 agent reaches
PHASE_4_TERMINAL it mints a Tombstone NFT carrying the IPFS CID of its
final MemoryBank. A successor V2 agent boots by fetching that bank and
injecting the last **K = 50** ticks into its initial reflection
context — the new agent's identity is partly its ancestor's lived
experience.

Sprint_1 ships the interface as a NotImplementedError stub; the IPFS
fetch + schema-validate + reflection-context wiring lands in sprint_2+
alongside the chain adapter and the production LLM integration
(Gemini 3.1 Flash Lite via google-genai; concrete client in
agent/llm/gemini_client.py sprint_4 T-B-006).
"""

from __future__ import annotations

from typing import Final

# PRD §13 canonical depth — last K ticks of ancestor lineage injected
# into successor reflection context. Bump only via Advisor + User
# approval, since it propagates to the V2 narrative seeding.
ANCESTOR_TICK_INJECTION_DEPTH: Final[int] = 50


def boot_from_ancestor(memory_bank_cid: str) -> None:
    """Fetch ancestor MemoryBank by CID + seed reflection context.

    Sprint_2+ implementation will:

    1. Resolve ``memory_bank_cid`` via the configured IPFS gateway.
    2. Stream the ancestor's tick directory.
    3. Validate each tick against
       ``.dev/contracts/memory_bank_schema.v1.0.0.json``.
    4. Apply :mod:`agent.core.memory_bank_migrations` upgrades.
    5. Select the last :data:`ANCESTOR_TICK_INJECTION_DEPTH` ticks.
    6. Prepend them to the new agent's reflection context per PRD §13.

    Sprint_1 keeps the signature stable so dependent modules (Track A's
    Tombstone consumer, the Reflection engine) can compile against it
    without circular imports.
    """
    raise NotImplementedError(
        "v2_boot.boot_from_ancestor lands in sprint_2 — see PRD §13 for the "
        f"K={ANCESTOR_TICK_INJECTION_DEPTH} ancestor-tick injection contract"
    )
