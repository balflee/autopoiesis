import asyncio

import pytest

from agent.runtime.incarnation_supervisor import (
    IncarnationManifest,
    LiveIncarnationSupervisor,
    read_manifest,
    write_manifest,
)
from agent.runtime.sandbox_phase2_loop import RunSummary


def _summary(*, died: bool) -> RunSummary:
    return RunSummary(
        ticks_completed=1, bets_placed=0, no_bets_emitted=1,
        settlements_processed=0, died=died, death_receipt=None,
        final_breath=0.0 if died else 50.0, final_bankroll_usd=0.0,
    )


class _FakeLoop:
    """Records the idx/adapter it was built with; run() returns a preset summary."""

    def __init__(self, *, idx, chain_adapter, summary, on_run=None):
        self._incarnation_number = idx
        self._chain_adapter = chain_adapter
        self._weights = f"weights-{idx}"
        self._summary = summary
        self._on_run = on_run

    @property
    def weights(self):
        return self._weights

    async def run(self):
        if self._on_run is not None:
            await self._on_run()
        return self._summary


def _make_supervisor(tmp_path, *, summaries, max_incarnations=10, on_run=None):
    built: list[dict] = []
    adapters: list[object] = []

    def build_chain_adapter():
        a = object()
        adapters.append(a)
        return a

    def build_incarnation(*, incarnation_idx, chain_adapter, initial_weights, incarnation_number):
        loop = _FakeLoop(idx=incarnation_idx, chain_adapter=chain_adapter,
                         summary=summaries[incarnation_idx], on_run=on_run)
        built.append({"idx": incarnation_idx, "adapter": chain_adapter,
                      "initial_weights": initial_weights})
        return loop

    sup = LiveIncarnationSupervisor(
        build_incarnation=build_incarnation,
        build_chain_adapter=build_chain_adapter,
        state_dir=tmp_path,
        max_incarnations=max_incarnations,
    )
    return sup, built, adapters


def test_respawns_on_death_until_survival(tmp_path):
    sup, built, adapters = _make_supervisor(
        tmp_path, summaries=[_summary(died=True), _summary(died=True), _summary(died=False)])
    asyncio.run(sup.run())
    assert [b["idx"] for b in built] == [0, 1, 2]
    # fresh chain_adapter per life (the re-die bug guard)
    assert len({id(a) for a in adapters}) == 3
    # weights carried: life1/life2 built with the prior life's terminal weights
    assert built[1]["initial_weights"] == "weights-0"
    assert built[2]["initial_weights"] == "weights-1"


def test_max_incarnations_cap(tmp_path):
    sup, built, _ = _make_supervisor(
        tmp_path, summaries=[_summary(died=True)] * 5, max_incarnations=3)
    asyncio.run(sup.run())
    assert [b["idx"] for b in built] == [0, 1, 2]  # capped at 3 lives


def test_cancel_propagates_and_does_not_respawn(tmp_path):
    async def boom():
        raise asyncio.CancelledError

    sup, built, _ = _make_supervisor(
        tmp_path, summaries=[_summary(died=True)] * 3, on_run=boom)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(sup.run())
    assert [b["idx"] for b in built] == [0]  # cancelled during life 0 → no respawn


def test_writes_manifest_each_transition(tmp_path):
    sup, _, _ = _make_supervisor(
        tmp_path, summaries=[_summary(died=True), _summary(died=False)])
    asyncio.run(sup.run())
    m = read_manifest(tmp_path)
    assert m is not None and m.current_incarnation_idx >= 1


def test_resumes_from_manifest_on_boot(tmp_path):
    # pre-seed a manifest at idx 2 → the supervisor's first life is incarnation 2
    write_manifest(tmp_path, IncarnationManifest(
        run_id="r", current_incarnation_idx=2, carry_weights_hash="x", max_incarnations=10))
    summaries = {0: _summary(died=True), 1: _summary(died=True), 2: _summary(died=False)}
    sup, built, _ = _make_supervisor(tmp_path, summaries=summaries)
    asyncio.run(sup.run())
    assert built[0]["idx"] == 2  # resumed, not restarted at 0
