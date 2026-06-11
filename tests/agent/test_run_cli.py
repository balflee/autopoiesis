"""CLI smoke tests for ``python -m agent.main``.

Targets the acceptance criterion ``python -m agent.main --help`` prints
non-empty usage + exits 0. We exercise the parser directly + via
subprocess so both the in-process import path and the module-execution
path are covered.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from agent.main import build_parser, cmd_run, main


def test_help_via_subprocess_exits_zero_and_prints_usage() -> None:
    """python -m agent.main --help must exit 0 with non-empty stdout."""
    proc = subprocess.run(
        [sys.executable, "-m", "agent.main", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, (
        f"--help should exit 0, got {proc.returncode}; stderr=\n{proc.stderr}"
    )
    # Usage strings must contain the program name + the description fragment.
    assert "agent" in proc.stdout.lower()
    assert "memory-bank" in proc.stdout.lower() or "memory_bank" in proc.stdout.lower()
    assert len(proc.stdout) > 100, "help text suspiciously short"


def test_bare_invocation_prints_help() -> None:
    """Calling main() with no args prints help + exits 0 (operator-friendly)."""
    rc = main(argv=[])
    assert rc == 0


def test_parser_registers_run_and_inspect_subcommands() -> None:
    """Both required subcommands MUST be registered."""
    parser = build_parser()
    # argparse stores subparsers under a private attr; introspect via
    # the parser's known actions to avoid coupling to argparse internals.
    sub_action = next(
        (a for a in parser._actions if a.dest == "command"),  # type: ignore[attr-defined]
        None,
    )
    assert sub_action is not None, "no subparsers registered"
    choices = sub_action.choices  # type: ignore[attr-defined]
    assert "run" in choices
    assert "inspect-memory-bank" in choices


def test_inspect_memory_bank_runs_on_empty_root(tmp_path: Path) -> None:
    """inspect-memory-bank on a fresh root must succeed and report count=0."""
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent.main",
            "--memory-bank-root",
            str(tmp_path / "mb"),
            "inspect-memory-bank",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, f"stderr=\n{proc.stderr}"
    assert '"count": 0' in proc.stdout


def test_run_stub_raises_not_implemented() -> None:
    """Sprint_1 contract: ``agent run`` body lands in sprint_2. Exercised
    in-process so the test doesn't pay for another interpreter cold start."""
    args = build_parser().parse_args(["run", "--once"])
    with pytest.raises(NotImplementedError, match="sprint_2"):
        cmd_run(args)


if __name__ == "__main__":  # pragma: no cover - allow direct invocation
    raise SystemExit(pytest.main([__file__, "-v"]))
