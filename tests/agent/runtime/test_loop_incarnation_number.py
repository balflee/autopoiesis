import inspect

from agent.runtime import sandbox_phase2_loop as L


def test_constructor_accepts_incarnation_number():
    sig = inspect.signature(L.SandboxPhase2Loop.__init__)
    assert "incarnation_number" in sig.parameters
    assert sig.parameters["incarnation_number"].default == 0  # default 0 = byte-identical off


def test_all_snapshot_sites_stamp_incarnation_number():
    src = inspect.getsource(L.SandboxPhase2Loop)
    # every AgentStateSnapshot(...) construction must pass incarnation_number=
    n_snapshots = src.count("AgentStateSnapshot(")
    n_stamped = src.count("incarnation_number=self._incarnation_number")
    assert n_snapshots >= 4, f"expected >=4 snapshot sites, found {n_snapshots}"
    assert n_stamped == n_snapshots, (
        f"{n_snapshots} AgentStateSnapshot sites but only {n_stamped} stamp "
        "incarnation_number — every site must stamp it"
    )
