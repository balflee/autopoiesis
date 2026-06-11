"""App-startup helpers for the Railway-mounted ``/data`` volume (T-B-038)
+ production loop factory configuration (T-B-041).

Responsibilities, all load-bearing for the dogfood loop the CEO
direction D-S11-001 §scope-decisions §7 locks in:

* :func:`validate_state_paths` — enforce the "no silent fallback" rule.
  If the production deploy expects ``/data`` to be a Railway volume
  mount and the mount is missing, raise a *clear* :class:`RuntimeError`
  at app build time with a remediation hint. The rule fires only when
  the operator has NOT explicitly overridden the path (i.e. the default
  ``/data/...`` is in play). When the operator points an env var at a
  local dir, we trust them — the explicit override is the opt-in escape
  hatch the brief calls out ("set ``BACKTEST_OUTPUT_ROOT=./local_state``
  or mount /data").

* :func:`prime_volume_cache` — one-shot copy of the image-baked seed
  cache (``agent/backtest/_cache/*.json``) into the volume's cache dir
  on first boot. Idempotent: subsequent boots are a no-op against any
  file whose target mtime is ≥ the source mtime. That lets:

  - first boot prime an empty volume,
  - a code release that *updates* a seed cassette overwrite the stale
    operator copy (source mtime > target mtime),
  - a same-image reboot leave every cached file untouched.

* :func:`resolve_prod_loop_config` — read the three sprint_13 T-B-041
  env knobs (``PROD_LOOP_TICK_INTERVAL_SECONDS``,
  ``PROD_LOOP_TIME_COMPRESSION``, ``PROD_LOOP_CHAIN_ADAPTER_KIND``)
  and surface them as a :class:`ResolvedProdLoopConfig`. The
  defaults pin every operator deploy to the canonical sandbox
  scaffold (60-second wall-clock tick cadence, 1.0x compression,
  in-memory ``SandboxLoopChainAdapter`` fake). T-B-042 will flip the
  chain-adapter kind to ``'rh_chain'`` once the real adapter ships.

All helpers are pure-Python + stdlib only — no FastAPI / Pydantic /
Polymarket imports — so they can run inside :func:`agent.server.main._build_default_app`
without dragging the heavy import surface into pytest collection (the
test conftest still opts out via ``GENESIS_SERVER_AUTOBUILD=0``).
"""

from __future__ import annotations

import logging
import math
import os
import shutil
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Public constants — the env-var contract surface
# --------------------------------------------------------------------------- #


SANDBOX_STATE_DIR_ENV_VAR: Final[str] = "SANDBOX_STATE_DIR"
"""Sandbox loop JSONL streams + snapshot root."""


BACKTEST_OUTPUT_ROOT_ENV_VAR: Final[str] = "BACKTEST_OUTPUT_ROOT"
"""Per-run sweep output dir root (one subdir per ``run_id``)."""


BACKTEST_CACHE_DIR_ENV_VAR: Final[str] = "BACKTEST_CACHE_DIR"
"""Seeded Polymarket market cassette source for the sweep runner."""


DEFAULT_SANDBOX_STATE_DIR: Final[Path] = Path("/data/sandbox")
DEFAULT_BACKTEST_OUTPUT_ROOT: Final[Path] = Path("/data/backtest/runs")
DEFAULT_BACKTEST_CACHE_DIR: Final[Path] = Path("/data/backtest/cache")

_VOLUME_ROOT: Final[Path] = Path("/data")
"""Railway mount path. The "no silent fallback" gate fires when ANY of
the three env vars is unset AND this directory does not exist."""


SEED_CACHE_SOURCE: Final[Path] = (
    Path(__file__).resolve().parents[1] / "backtest" / "_cache"
)
"""Image-baked seed cache the bootstrap copies into the volume on first
boot. Resolves to ``<repo>/agent/backtest/_cache`` under both pytest
(worktree) and the container runtime (``/app/agent/backtest/_cache``)."""


# --------------------------------------------------------------------------- #
# T-B-041 — production loop factory configuration env knobs (sprint_13)
# --------------------------------------------------------------------------- #


PROD_LOOP_TICK_INTERVAL_SECONDS_ENV_VAR: Final[str] = (
    "PROD_LOOP_TICK_INTERVAL_SECONDS"
)
"""Wall-clock cadence (seconds) between consecutive production-loop ticks.

The default — 60 — is the sprint_13 sandbox cadence: short enough that
operators see decisions stream on the dashboard within a minute of
clicking *Start Agent*, long enough that the loop's per-tick work
(BREATH read, settlement poll, decision fusion, JSONL writes) does NOT
race the next tick. PRD §6.4's canonical 45-min decision cadence still
applies on the long-running live-money rollout; this knob is the
sandbox shorthand that lets sprint_13 prove the wiring at human time
scales without changing the spec default."""


DEFAULT_PROD_LOOP_TICK_INTERVAL_SECONDS: Final[float] = 60.0


PROD_LOOP_TIME_COMPRESSION_ENV_VAR: Final[str] = "PROD_LOOP_TIME_COMPRESSION"
"""Speed-up factor applied to :data:`PROD_LOOP_TICK_INTERVAL_SECONDS_ENV_VAR`.

Default 1.0 — wall-clock tick cadence equals the env-var literal. A
value >1.0 divides the cadence (e.g. ``2.0`` halves the sleep), which
lets sandbox smoke runs + integration tests exercise the loop at
seconds-per-tick speeds without rebuilding the image. Values ≤ 0 are
rejected by :func:`resolve_prod_loop_config` to prevent a
divide-by-zero or sign-flipped cadence."""


DEFAULT_PROD_LOOP_TIME_COMPRESSION: Final[float] = 1.0


PROD_LOOP_CHAIN_ADAPTER_KIND_ENV_VAR: Final[str] = (
    "PROD_LOOP_CHAIN_ADAPTER_KIND"
)
"""Switch picking the :class:`agent.runtime.sandbox_phase2_loop.SandboxLoopChainAdapter`
implementation the production loop binds against.

Allowed values:

* ``'sandbox'`` (default, sprint_13 T-B-041) — the in-memory
  ``_SandboxChainAdapter`` scaffold lives in :mod:`agent.server.main`.
  Tracks BREATH locally; ``read_breath`` returns the in-process
  scalar; ``kill_and_mint_tombstone`` returns a deterministic
  placeholder :class:`DeathReceipt`. Sufficient for the loop to boot
  end-to-end (real ticks, real fusion, real NO_BET decisions) without
  any chain RPC.
* ``'rh_chain'`` (sprint_13 T-B-042) — the real Polygon RPC-backed
  chain adapter. NOT WIRED YET; resolving this kind today raises
  :class:`NotImplementedError` with a forward-pointer to T-B-042.

The default is sandbox so operators flipping the deploy gate on a
volume-mounted Railway service get the prod loop booting immediately
(no env-var change required); T-B-042 will flip the default once the
RPC wiring lands."""


DEFAULT_PROD_LOOP_CHAIN_ADAPTER_KIND: Final[str] = "sandbox"


PROD_LOOP_CHAIN_ADAPTER_KIND_SANDBOX: Final[str] = "sandbox"
PROD_LOOP_CHAIN_ADAPTER_KIND_RH_CHAIN: Final[str] = "rh_chain"
PROD_LOOP_CHAIN_ADAPTER_KINDS: Final[frozenset[str]] = frozenset(
    {
        PROD_LOOP_CHAIN_ADAPTER_KIND_SANDBOX,
        PROD_LOOP_CHAIN_ADAPTER_KIND_RH_CHAIN,
    }
)


@dataclass(frozen=True)
class ResolvedProdLoopConfig:
    """Resolved sprint_13 production-loop configuration knobs.

    Populated by :func:`resolve_prod_loop_config`. The
    :func:`agent.server.main._build_default_app` reads this once at app
    build time and threads the three fields into
    :func:`agent.server.main._build_production_loop_factory`.

    Attributes
    ----------
    tick_interval_seconds
        Wall-clock seconds between two consecutive loop ticks BEFORE
        the time-compression divisor is applied. Constrained to > 0.

    time_compression
        Divisor applied to ``tick_interval_seconds`` to derive the
        loop's effective :class:`datetime.timedelta` ``decision_cadence``.
        1.0 = literal seconds; >1.0 = faster ticks. Constrained to > 0.

    chain_adapter_kind
        Either ``'sandbox'`` or ``'rh_chain'`` — case-folded at parse
        time. ``rh_chain`` is reserved for T-B-042; selecting it today
        raises :class:`NotImplementedError` in the adapter factory.
    """

    tick_interval_seconds: float
    time_compression: float
    chain_adapter_kind: str


# --------------------------------------------------------------------------- #
# Resolved-paths value object
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ResolvedStatePaths:
    """Resolved on-disk targets the FastAPI app wires its runtime against.

    The ``*_explicit`` flags record whether the operator opted in via an
    env var (vs falling through to the /data/* default). The fallback
    gate (:func:`validate_state_paths`) reads them: an explicit override
    means the operator knows what they're doing, so the missing-volume
    check is skipped.
    """

    sandbox_state_dir: Path
    backtest_output_root: Path
    backtest_cache_dir: Path
    sandbox_state_dir_explicit: bool
    backtest_output_root_explicit: bool
    backtest_cache_dir_explicit: bool


# --------------------------------------------------------------------------- #
# Env-var resolution + the "no silent fallback" gate
# --------------------------------------------------------------------------- #


def _resolve_one(
    env_var: str, default: Path, env: Mapping[str, str]
) -> tuple[Path, bool]:
    """Empty / whitespace-only env values count as unset — guards against an
    operator clearing the variable in the Railway UI then unintentionally
    inheriting the default."""
    raw = env.get(env_var, "")
    if raw.strip():
        return Path(raw), True
    return default, False


def resolve_state_paths(
    env: Mapping[str, str] | None = None,
) -> ResolvedStatePaths:
    """Read the three path env vars + return the resolved targets.

    Pure function — no FS side effects. Caller is responsible for
    invoking :func:`validate_state_paths` before any mkdir / write,
    so a misconfigured deploy fails LOUD at app build time instead of
    silently mkdir'ing ``/data/...`` under the container's writable
    layer (which gets blown away by the next Railway redeploy).
    """
    e = env if env is not None else os.environ
    sandbox, sandbox_explicit = _resolve_one(
        SANDBOX_STATE_DIR_ENV_VAR, DEFAULT_SANDBOX_STATE_DIR, e
    )
    backtest_out, backtest_out_explicit = _resolve_one(
        BACKTEST_OUTPUT_ROOT_ENV_VAR, DEFAULT_BACKTEST_OUTPUT_ROOT, e
    )
    backtest_cache, backtest_cache_explicit = _resolve_one(
        BACKTEST_CACHE_DIR_ENV_VAR, DEFAULT_BACKTEST_CACHE_DIR, e
    )
    return ResolvedStatePaths(
        sandbox_state_dir=sandbox,
        backtest_output_root=backtest_out,
        backtest_cache_dir=backtest_cache,
        sandbox_state_dir_explicit=sandbox_explicit,
        backtest_output_root_explicit=backtest_out_explicit,
        backtest_cache_dir_explicit=backtest_cache_explicit,
    )


def _any_default_in_use(paths: ResolvedStatePaths) -> bool:
    return not (
        paths.sandbox_state_dir_explicit
        and paths.backtest_output_root_explicit
        and paths.backtest_cache_dir_explicit
    )


_MISSING_VOLUME_HINT: Final[str] = (
    "Genesis agent cannot start: at least one state-path env var defaulted "
    "to /data/* but /data does not exist. Remediation:\n"
    "  * Production (Railway): provision the 'autopoiesis-state' volume in "
    "the Railway UI and redeploy — see docs/DEPLOYMENT.md "
    "'Railway Volume Provisioning'.\n"
    "  * Local dev: either `mkdir -p /data/{sandbox,backtest/runs,backtest/cache}` "
    "OR explicitly point the three env vars at a local root, e.g.:\n"
    "      export SANDBOX_STATE_DIR=./local_state/sandbox\n"
    "      export BACKTEST_OUTPUT_ROOT=./local_state/backtest/runs\n"
    "      export BACKTEST_CACHE_DIR=./local_state/backtest/cache"
)


def _resolve_positive_float(
    *,
    env_var: str,
    default: float,
    env: Mapping[str, str],
) -> float:
    """Parse a strictly-positive float from ``env``.

    Empty / whitespace-only / missing → default. Non-parseable or
    non-positive → :class:`RuntimeError` with the offending value
    surfaced so the operator can spot the typo in the deploy log
    (mirrors the loud-fail posture :func:`validate_state_paths` uses
    for the volume gate)."""
    raw = env.get(env_var, "")
    if not raw.strip():
        return default
    try:
        parsed = float(raw)
    except ValueError as exc:
        raise RuntimeError(
            f"{env_var}={raw!r} is not a valid float — set a positive "
            f"number or unset to use the default ({default})."
        ) from exc
    if not (math.isfinite(parsed) and parsed > 0.0):
        raise RuntimeError(
            f"{env_var}={raw!r} must be strictly positive and finite "
            f"(got {parsed}); unset to use the default ({default})."
        )
    return parsed


def resolve_prod_loop_config(
    env: Mapping[str, str] | None = None,
) -> ResolvedProdLoopConfig:
    """Read the three sprint_13 T-B-041 env knobs + return resolved config.

    Pure function — no side effects. Caller is
    :func:`agent.server.main._build_default_app`; it threads the
    returned :class:`ResolvedProdLoopConfig` into
    :func:`agent.server.main._build_production_loop_factory`.

    Validation:

    * Numeric env vars (``PROD_LOOP_TICK_INTERVAL_SECONDS``,
      ``PROD_LOOP_TIME_COMPRESSION``) must parse as a strictly positive
      finite float; otherwise the helper raises :class:`RuntimeError`
      with the offending value surfaced in the message.
    * ``PROD_LOOP_CHAIN_ADAPTER_KIND`` is case-folded and must be one
      of :data:`PROD_LOOP_CHAIN_ADAPTER_KINDS`. Unknown values raise
      :class:`RuntimeError` listing the allowed kinds.

    The validation is loud-on-misconfiguration by design — the brief's
    "no silent fallback" rule (D-S11-001) extends from the volume gate
    to every operator-tweakable knob the loop factory consumes."""
    e = env if env is not None else os.environ
    tick_interval = _resolve_positive_float(
        env_var=PROD_LOOP_TICK_INTERVAL_SECONDS_ENV_VAR,
        default=DEFAULT_PROD_LOOP_TICK_INTERVAL_SECONDS,
        env=e,
    )
    time_compression = _resolve_positive_float(
        env_var=PROD_LOOP_TIME_COMPRESSION_ENV_VAR,
        default=DEFAULT_PROD_LOOP_TIME_COMPRESSION,
        env=e,
    )
    raw_kind = e.get(PROD_LOOP_CHAIN_ADAPTER_KIND_ENV_VAR, "").strip().lower()
    kind = raw_kind or DEFAULT_PROD_LOOP_CHAIN_ADAPTER_KIND
    if kind not in PROD_LOOP_CHAIN_ADAPTER_KINDS:
        raise RuntimeError(
            f"{PROD_LOOP_CHAIN_ADAPTER_KIND_ENV_VAR}={raw_kind!r} is not a "
            f"recognised kind. Allowed: "
            f"{sorted(PROD_LOOP_CHAIN_ADAPTER_KINDS)}; unset to use the "
            f"default ({DEFAULT_PROD_LOOP_CHAIN_ADAPTER_KIND!r})."
        )
    return ResolvedProdLoopConfig(
        tick_interval_seconds=tick_interval,
        time_compression=time_compression,
        chain_adapter_kind=kind,
    )


def validate_state_paths(
    paths: ResolvedStatePaths,
    *,
    volume_root: Path = _VOLUME_ROOT,
) -> None:
    """Enforce the "no silent fallback to /tmp" rule (T-B-038 brief).

    Fires :class:`RuntimeError` when ANY path defaulted (operator did
    NOT explicitly override) AND the volume root does not exist. The
    message embeds a verbatim remediation hint so the operator can
    paste a fix straight from the deploy log.

    The check is BEFORE :func:`prime_volume_cache` + before the per-dir
    mkdir, so a misconfigured deploy doesn't pollute the container's
    writable layer with throwaway /data/* dirs that vanish on the next
    redeploy.

    ``volume_root`` is parameterised purely for unit testing — production
    always uses the module-level ``/data``.
    """
    if _any_default_in_use(paths) and not volume_root.exists():
        raise RuntimeError(_MISSING_VOLUME_HINT)


# --------------------------------------------------------------------------- #
# Idempotent cache prime
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PrimeResult:
    """Return value from :func:`prime_volume_cache`.

    ``copied`` lists the basenames the helper actually copied (first
    boot, or a code release with newer seed mtimes); ``skipped`` lists
    the basenames it left in place because the volume already held a
    same-or-newer file. Both lists are sorted so the operator log line
    is deterministic.
    """

    copied: tuple[str, ...]
    skipped: tuple[str, ...]


def _iter_seed_files(source: Path) -> Iterable[Path]:
    """Yield every ``*.json`` cassette under ``source`` in sorted order.

    The ``_cache`` source dir also carries a ``.gitkeep`` — filtering
    on ``*.json`` excludes it without an explicit allowlist.
    """
    if not source.exists():
        return ()
    return sorted(source.glob("*.json"))


def prime_volume_cache(
    *,
    source: Path = SEED_CACHE_SOURCE,
    target: Path,
) -> PrimeResult:
    """One-shot copy of seeded ``*.json`` cassettes — idempotent.

    Rules:

    * Target dir is created with ``parents=True`` (no-op if it exists).
    * For each ``*.json`` under ``source``:
      - if the target file does NOT exist: copy.
      - if it exists AND target mtime ≥ source mtime: skip (the volume
        already holds a same-or-newer copy — likely an operator-edited
        cassette or a same-image reboot).
      - otherwise (source mtime > target mtime): overwrite. A code
        release with refreshed seed data takes precedence over a
        stale primed file.
    * Source missing OR empty: returns an empty :class:`PrimeResult` —
      nothing to prime is not an error (operator may have seeded the
      volume out-of-band).

    The helper does NOT touch files in ``target`` whose basename has no
    corresponding source entry — operator-uploaded cassettes survive
    untouched.

    Cross-platform: uses :func:`shutil.copy2` so mtime metadata is
    preserved on the target. Windows + Linux both honour the mtime
    comparison, so the idempotency contract holds in unit tests run
    on a developer Windows box AND on the Railway Linux container.
    """
    target.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    skipped: list[str] = []
    for src_path in _iter_seed_files(source):
        name = src_path.name
        dst_path = target / name
        if dst_path.exists():
            try:
                src_mtime = src_path.stat().st_mtime
                dst_mtime = dst_path.stat().st_mtime
            except OSError:
                logger.warning(
                    "bootstrap: failed to stat %s or %s — skipping",
                    src_path, dst_path,
                )
                skipped.append(name)
                continue
            if dst_mtime >= src_mtime:
                skipped.append(name)
                continue
        shutil.copy2(src_path, dst_path)
        copied.append(name)
    result = PrimeResult(
        copied=tuple(copied),
        skipped=tuple(skipped),
    )
    if copied or skipped:
        logger.info(
            "bootstrap: primed backtest cache target=%s copied=%d skipped=%d",
            target, len(copied), len(skipped),
        )
    return result


__all__ = [
    "BACKTEST_CACHE_DIR_ENV_VAR",
    "BACKTEST_OUTPUT_ROOT_ENV_VAR",
    "DEFAULT_BACKTEST_CACHE_DIR",
    "DEFAULT_BACKTEST_OUTPUT_ROOT",
    "DEFAULT_PROD_LOOP_CHAIN_ADAPTER_KIND",
    "DEFAULT_PROD_LOOP_TICK_INTERVAL_SECONDS",
    "DEFAULT_PROD_LOOP_TIME_COMPRESSION",
    "DEFAULT_SANDBOX_STATE_DIR",
    "PROD_LOOP_CHAIN_ADAPTER_KINDS",
    "PROD_LOOP_CHAIN_ADAPTER_KIND_ENV_VAR",
    "PROD_LOOP_CHAIN_ADAPTER_KIND_RH_CHAIN",
    "PROD_LOOP_CHAIN_ADAPTER_KIND_SANDBOX",
    "PROD_LOOP_TICK_INTERVAL_SECONDS_ENV_VAR",
    "PROD_LOOP_TIME_COMPRESSION_ENV_VAR",
    "SANDBOX_STATE_DIR_ENV_VAR",
    "SEED_CACHE_SOURCE",
    "PrimeResult",
    "ResolvedProdLoopConfig",
    "ResolvedStatePaths",
    "prime_volume_cache",
    "resolve_prod_loop_config",
    "resolve_state_paths",
    "validate_state_paths",
]
