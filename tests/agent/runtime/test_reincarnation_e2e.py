import asyncio

from agent.data.sandbox_state import DEATHS_FILENAME, iter_jsonl
from agent.runtime.incarnation_supervisor import LiveIncarnationSupervisor
from agent.runtime.sandbox_phase2_loop import RunSummary


class _DyingLoop:
    """Minimal loop that appends a DeathRecord to the ROOT deaths.jsonl on run()
    (mimicking the real _SandboxStateHook on death) then returns died=True."""

    def __init__(self, *, state_dir, idx):
        self._state_dir = state_dir
        self._incarnation_number = idx
        self._weights = f"w{idx}"

    @property
    def weights(self):
        return self._weights

    async def run(self):
        from agent.data.sandbox_state import DeathRecord, SandboxStateWriter

        w = SandboxStateWriter(root=self._state_dir)
        w.append_death(DeathRecord(
            death_id=f"d{self._incarnation_number}", ts="t",
            incarnation_number=self._incarnation_number,
            agent_id="a", last_tick=1, final_bankroll_usd=0.0,
        ))
        return RunSummary(
            ticks_completed=1, bets_placed=0, no_bets_emitted=0,
            settlements_processed=0, died=True, death_receipt=None,
            final_breath=0.0, final_bankroll_usd=0.0,
        )


def test_deaths_accumulate_across_incarnations(tmp_path):
    sup = LiveIncarnationSupervisor(
        build_incarnation=lambda *, incarnation_idx, chain_adapter, initial_weights, incarnation_number: _DyingLoop(
            state_dir=tmp_path, idx=incarnation_idx,
        ),
        build_chain_adapter=lambda: object(),
        state_dir=tmp_path,
        max_incarnations=3,
    )
    asyncio.run(sup.run())
    deaths = iter_jsonl(tmp_path / DEATHS_FILENAME)
    # 3 incarnations all died → 3 lineage rows with incarnation_number 0,1,2.
    # deaths.jsonl is NOT in _PER_LIFE_STREAMS, so it is never reset — it
    # accumulates (the lineage/treasury cumulative invariant).
    assert [d["incarnation_number"] for d in deaths] == [0, 1, 2]
