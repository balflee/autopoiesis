"""Sprint_1 smoke tests for sim/replay.py.

Acceptance criteria covered (per T-C-001 task brief):

* :func:`sim.replay.load_tarball` exposes the exact signature
  ``(path: Path) -> list[TickRecord]``.
* Calling it raises ``NotImplementedError('sprint_2')``.
* The docstring references :file:`agent/core/memory_bank_schema.json`,
  PRD §14, and TECHNICAL_PLAN.md §4.6 — the calibration validator
  greps these anchors to confirm the consumer slot is wired.
"""

from __future__ import annotations

import inspect
import typing
from pathlib import Path

import pytest

from sim.replay import TickRecord, load_tarball


def test_load_tarball_raises_sprint_2(tmp_path: Path) -> None:
    """Sprint_1 contract: the stub raises with the literal message
    ``sprint_2`` so the validator can grep for it."""
    fake = tmp_path / "memory_bank.tar.gz"
    fake.write_bytes(b"")  # path must be a real Path; content irrelevant
    with pytest.raises(NotImplementedError) as exc:
        load_tarball(fake)
    assert str(exc.value) == "sprint_2"


def test_load_tarball_signature_is_locked() -> None:
    """``load_tarball(path: Path) -> list[TickRecord]`` is the
    cross-track contract; downstream sim modules import this signature
    today even though the body is sprint_2.

    Because ``sim.replay`` uses ``from __future__ import annotations``,
    runtime introspection sees string forms — we resolve via
    :func:`typing.get_type_hints` to compare the real types.
    """
    sig = inspect.signature(load_tarball)
    params = list(sig.parameters.values())
    assert len(params) == 1
    assert params[0].name == "path"
    hints = typing.get_type_hints(load_tarball)
    # Path annotation MUST be present so mypy --strict accepts callers.
    assert hints["path"] is Path
    # Return type must be ``list[TickRecord]``.
    return_hint = hints["return"]
    assert typing.get_origin(return_hint) is list
    (item_type,) = typing.get_args(return_hint)
    assert item_type is TickRecord


def test_load_tarball_docstring_cites_anchors() -> None:
    """Track C calibration validator (DEV_FRAMEWORK §26 T2.7) greps
    the docstring for the three anchor references; missing any one is
    a calibration FAIL."""
    doc = load_tarball.__doc__ or ""
    assert "agent/core/memory_bank_schema.json" in doc
    assert "PRD" in doc and "14" in doc
    assert "TECHNICAL_PLAN" in doc and "4.6" in doc


def test_tick_record_shape_is_present() -> None:
    """Sprint_1 placeholder shape — sprint_2 swaps fields, but the
    dataclass must exist today so callers can import it."""
    record = TickRecord(tick=0, raw={"hello": "world"})
    assert record.tick == 0
    assert record.raw == {"hello": "world"}
