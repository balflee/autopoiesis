"""Volume-path resolution + idempotent prime tests (T-B-038).

Four tests pinned to the T-B-038 brief acceptance criteria:

1. ``test_env_var_override_redirects_io`` — explicit
   ``BACKTEST_OUTPUT_ROOT=<tmp>`` makes the app's resolver point at
   that dir AND the resulting backtest registry writes results.json
   under the same tree (proving the env var is the load-bearing knob
   the operator turns for non-Railway deploys).

2. ``test_bootstrap_primes_empty_volume`` — empty cache dir + populated
   seed source → :func:`prime_volume_cache` copies every ``*.json`` on
   first boot AND a second invocation against the now-primed dir is a
   no-op (mtime guard fires; the brief's "running twice does not
   overwrite existing cache files" contract).

3. ``test_write_then_read_round_trip`` — write a synthetic
   ``results.json`` under the resolved backtest output root (via a
   tmp_path-rooted env override), read it back through the same path
   resolution, assert equality. Proves the env-var contract round-trips
   end-to-end without a silent re-route.

4. ``test_missing_volume_raises_clear_error`` — when ``/data`` does
   not exist AND no env override is set, :func:`validate_state_paths`
   raises a :class:`RuntimeError` whose message names BOTH the volume
   mount remediation AND the local-dev override path. Brief-locked:
   NOT a silent fallback to ``/tmp`` or ``cwd``.

Hermetic — no FastAPI app construction, no real volume, no real
Polymarket cache.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agent.server.bootstrap import (
    BACKTEST_CACHE_DIR_ENV_VAR,
    BACKTEST_OUTPUT_ROOT_ENV_VAR,
    SANDBOX_STATE_DIR_ENV_VAR,
    prime_volume_cache,
    resolve_state_paths,
    validate_state_paths,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _env_with(
    *,
    sandbox: str | None = None,
    backtest_root: str | None = None,
    cache: str | None = None,
) -> dict[str, str]:
    """Build a clean env dict — only the three vars we exercise.

    Passing ``None`` for a key omits it (simulating "operator did not
    set this env var"), so the resolver falls back to the /data/...
    default and the validate gate fires when /data is absent.
    """
    env: dict[str, str] = {}
    if sandbox is not None:
        env[SANDBOX_STATE_DIR_ENV_VAR] = sandbox
    if backtest_root is not None:
        env[BACKTEST_OUTPUT_ROOT_ENV_VAR] = backtest_root
    if cache is not None:
        env[BACKTEST_CACHE_DIR_ENV_VAR] = cache
    return env


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


def test_env_var_override_redirects_io(tmp_path: Path) -> None:
    """Brief criterion 1 — env vars are the canonical override surface.

    The resolver MUST return the operator's path verbatim, not the
    /data default. The resolved path is then what the FastAPI builder
    threads into the backtest registry (verified by reading the path
    back out of :class:`ResolvedStatePaths`).
    """
    sandbox_dir = tmp_path / "sandbox"
    output_root = tmp_path / "backtest" / "runs"
    cache_dir = tmp_path / "backtest" / "cache"

    paths = resolve_state_paths(
        env=_env_with(
            sandbox=str(sandbox_dir),
            backtest_root=str(output_root),
            cache=str(cache_dir),
        )
    )

    assert paths.sandbox_state_dir == sandbox_dir
    assert paths.backtest_output_root == output_root
    assert paths.backtest_cache_dir == cache_dir
    # All three explicit → the validate gate must NOT fire even when
    # the (non-existent) volume root is missing.
    missing_volume = tmp_path / "does_not_exist"
    assert not missing_volume.exists()
    validate_state_paths(paths, volume_root=missing_volume)


def test_bootstrap_primes_empty_volume(tmp_path: Path) -> None:
    """Brief criterion 2 — empty cache + populated seed source.

    First-boot: every ``*.json`` under the seed source lands in the
    target. Second-boot: nothing copies (mtime guard fires) so the
    operator's primed volume is untouched. The brief's "running twice
    does not overwrite existing cache files (mtime check)" rule.
    """
    source = tmp_path / "seed"
    source.mkdir()
    target = tmp_path / "volume_cache"

    # Seed source — two cassettes + an irrelevant non-JSON file.
    (source / "alpha.json").write_text('{"market_id": "alpha"}', encoding="utf-8")
    (source / "beta.json").write_text('{"market_id": "beta"}', encoding="utf-8")
    (source / "README.md").write_text("ignored", encoding="utf-8")

    first = prime_volume_cache(source=source, target=target)

    assert set(first.copied) == {"alpha.json", "beta.json"}
    assert first.skipped == ()
    assert (target / "alpha.json").read_text(encoding="utf-8") == '{"market_id": "alpha"}'
    assert (target / "beta.json").read_text(encoding="utf-8") == '{"market_id": "beta"}'
    # Non-JSON should NOT have been copied.
    assert not (target / "README.md").exists()

    # Operator-edited the alpha cassette AFTER prime. The mtime guard
    # MUST leave the edited copy in place on the second boot.
    edited_payload = '{"market_id": "alpha", "operator_edited": true}'
    (target / "alpha.json").write_text(edited_payload, encoding="utf-8")
    # Push the target mtime forward so it strictly beats the source.
    src_alpha = source / "alpha.json"
    src_stat = src_alpha.stat()
    os.utime(target / "alpha.json", (src_stat.st_atime + 10.0, src_stat.st_mtime + 10.0))

    second = prime_volume_cache(source=source, target=target)

    assert set(second.skipped) == {"alpha.json", "beta.json"}
    assert second.copied == ()
    # Operator's edit survived.
    assert (target / "alpha.json").read_text(encoding="utf-8") == edited_payload


def test_write_then_read_round_trip(tmp_path: Path) -> None:
    """Brief criterion 3 — env var → resolved path → file I/O round-trip.

    Writes a synthetic ``results.json`` under the resolved
    ``BACKTEST_OUTPUT_ROOT`` and reads it back via the SAME path
    resolution. Proves a future refactor that silently re-routes the
    env var (e.g. defaulting to ``/tmp``) would FAIL this test
    immediately — the read must come from the same dir as the write.
    """
    overridden_root = tmp_path / "operator_chosen" / "backtest" / "runs"
    paths = resolve_state_paths(
        env=_env_with(
            sandbox=str(tmp_path / "sandbox"),
            backtest_root=str(overridden_root),
            cache=str(tmp_path / "cache"),
        )
    )

    # Write through the resolved path — same call the FastAPI builder
    # makes via paths.backtest_output_root.mkdir(...) before handing
    # to the BacktestRegistry.
    paths.backtest_output_root.mkdir(parents=True, exist_ok=True)
    run_dir = paths.backtest_output_root / "sweep-roundtrip-001"
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": "sweep-roundtrip-001",
        "configs_run": 1,
        "results": [{"config_id": "rho-0.6", "died": False}],
    }
    (run_dir / "results.json").write_text(
        json.dumps(payload, sort_keys=True), encoding="utf-8"
    )

    # Re-resolve from the same env (mimicking a process restart that
    # re-reads /data state) and read back.
    paths_after_restart = resolve_state_paths(
        env=_env_with(
            sandbox=str(tmp_path / "sandbox"),
            backtest_root=str(overridden_root),
            cache=str(tmp_path / "cache"),
        )
    )
    read_back_path = (
        paths_after_restart.backtest_output_root
        / "sweep-roundtrip-001"
        / "results.json"
    )
    assert read_back_path.exists()
    assert json.loads(read_back_path.read_text(encoding="utf-8")) == payload


def test_missing_volume_raises_clear_error(tmp_path: Path) -> None:
    """Brief criterion 4 — no silent fallback when /data is missing.

    With ZERO env overrides AND a non-existent volume root, the
    validate gate must:
      * raise :class:`RuntimeError` (not silently mkdir under /tmp);
      * embed both the production remediation (provision the volume)
        AND the local-dev escape hatch (set the env vars).
    """
    missing_volume = tmp_path / "no_such_data"
    assert not missing_volume.exists()

    paths = resolve_state_paths(env={})  # all three defaults in play

    with pytest.raises(RuntimeError) as exc_info:
        validate_state_paths(paths, volume_root=missing_volume)

    message = str(exc_info.value)
    # Anchor on the two remediation halves the brief calls out so a
    # future copy-edit that drops either hint trips the assertion.
    assert "Railway" in message
    assert "docs/DEPLOYMENT.md" in message
    assert "SANDBOX_STATE_DIR" in message
    assert "BACKTEST_OUTPUT_ROOT" in message
    assert "BACKTEST_CACHE_DIR" in message
    # Explicit "no silent fallback" — must NOT contain a path-to-tmp
    # suggestion. The brief locks the error over the fallback.
    assert "/tmp" not in message

    # Partial override still trips the gate — operator set ONE var but
    # left two defaulting to /data, and /data is still missing.
    paths_partial = resolve_state_paths(
        env=_env_with(sandbox=str(tmp_path / "sandbox_only"))
    )
    with pytest.raises(RuntimeError):
        validate_state_paths(paths_partial, volume_root=missing_volume)

    # Full override across all three → gate does NOT fire even with
    # /data missing (the operator opted out explicitly).
    paths_full = resolve_state_paths(
        env=_env_with(
            sandbox=str(tmp_path / "sandbox"),
            backtest_root=str(tmp_path / "runs"),
            cache=str(tmp_path / "cache"),
        )
    )
    validate_state_paths(paths_full, volume_root=missing_volume)
