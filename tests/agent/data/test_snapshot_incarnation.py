from agent.data.sandbox_state import AgentStateSnapshot


def _base(**kw):
    d = dict(
        snapshot_ts="t",
        phase="PHASE_2_APPRENTICE",
        breath=50.0,
        bankroll_usd=100.0,
        phase_age_days=0.0,
    )
    d.update(kw)
    return d


def test_incarnation_defaults_zero():
    assert AgentStateSnapshot(**_base()).incarnation_number == 0


def test_incarnation_roundtrips():
    snap = AgentStateSnapshot(**_base(incarnation_number=3))
    reloaded = AgentStateSnapshot.model_validate_json(snap.model_dump_json())
    assert reloaded.incarnation_number == 3


def test_old_snapshot_without_field_still_loads():
    # a pre-P1 snapshot JSON has no incarnation_number key
    snap = AgentStateSnapshot(**_base())
    row = snap.model_dump()
    row.pop("incarnation_number", None)
    assert AgentStateSnapshot.model_validate(row).incarnation_number == 0
