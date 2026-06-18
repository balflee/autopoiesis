from agent.data.sandbox_state import (
    DEATHS_FILENAME,
    GODS_TREASURY_FILENAME,
    DeathRecord,
    SandboxStateWriter,
    TitheRecord,
    TributeRecord,
    iter_jsonl,
)


def test_append_tribute_and_tithe_interleave(tmp_path):
    w = SandboxStateWriter(root=tmp_path)
    w.append_tithe(
        TitheRecord(
            tithe_id="h1", ts="t", tick=20, paid_usd=20.0, breath_cost=0.0,
            breath_after=80.0, bankroll_after=980.0,
        )
    )
    w.append_tribute(
        TributeRecord(
            tribute_id="t1", ts="t", tick=40, amount_usd=2000.0, success=True,
            breath_after=35.0, bankroll_after=0.0, dice_roll=0.5,
        )
    )
    rows = iter_jsonl(tmp_path / GODS_TREASURY_FILENAME)
    assert [r["type"] for r in rows] == ["tithe", "tribute"]
    # the tithe row carries no dice_roll key (tithe has no such field)
    assert "dice_roll" not in rows[0]
    assert rows[1]["dice_roll"] == 0.5


def test_append_death(tmp_path):
    w = SandboxStateWriter(root=tmp_path)
    w.append_death(
        DeathRecord(
            death_id="d1", ts="t", agent_id="a", last_tick=9, final_bankroll_usd=1.0,
        )
    )
    rows = iter_jsonl(tmp_path / DEATHS_FILENAME)
    assert len(rows) == 1 and rows[0]["cause"] == "breath_zero"
