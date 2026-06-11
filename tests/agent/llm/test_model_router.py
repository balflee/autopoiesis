"""ModelRouter tests — single-model dispatch + future-proof overrides."""

from __future__ import annotations

from agent.llm.gemini_client import DEFAULT_GEMINI_MODEL
from agent.llm.model_router import ESCALATED_GEMINI_MODEL, ModelRouter


def test_default_router_routes_routine_to_flash_lite() -> None:
    """v1 ships single-model — routine calls hit Flash Lite."""
    router = ModelRouter()
    assert router.model_for(task="sentiment", key_moment=False) == DEFAULT_GEMINI_MODEL
    assert router.model_for(task="reflection", key_moment=False) == DEFAULT_GEMINI_MODEL


def test_default_router_routes_key_moment_to_escalated() -> None:
    """v1 escalated tier == routine tier (both Flash Lite). The constant
    is named separately so a later calibration sprint can bump it."""
    router = ModelRouter()
    assert (
        router.model_for(task="reflection", key_moment=True) == ESCALATED_GEMINI_MODEL
    )
    assert (
        router.model_for(task="sentiment", key_moment=True) == ESCALATED_GEMINI_MODEL
    )


def test_escalated_constant_is_flash_lite_for_v1() -> None:
    """Brief mandate: 'ships single-model for v1' — the escalated
    constant must match the routine one until a calibration sprint
    bumps it."""
    assert ESCALATED_GEMINI_MODEL == DEFAULT_GEMINI_MODEL
    assert ESCALATED_GEMINI_MODEL == "gemini-3.5-flash"


def test_router_accepts_explicit_overrides() -> None:
    """Future calibration override path — swap the model id without
    touching engine code."""
    router = ModelRouter(
        routine_model="gemini-3.1-flash-lite-canary",
        escalated_model="gemini-3.1-pro",
    )
    assert (
        router.model_for(task="reflection", key_moment=False)
        == "gemini-3.1-flash-lite-canary"
    )
    assert router.model_for(task="reflection", key_moment=True) == "gemini-3.1-pro"


def test_router_key_moment_axis_is_per_call() -> None:
    """The dispatch table reads ``key_moment`` per call so the same
    router instance can route both routine + escalated traffic without
    reconstruction."""
    router = ModelRouter(
        routine_model="m_routine",
        escalated_model="m_escalated",
    )
    decisions = [
        router.model_for(task="sentiment", key_moment=False),
        router.model_for(task="sentiment", key_moment=True),
        router.model_for(task="reflection", key_moment=False),
        router.model_for(task="reflection", key_moment=True),
    ]
    assert decisions == ["m_routine", "m_escalated", "m_routine", "m_escalated"]
