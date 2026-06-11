"""Forward-only migration chain for MemoryBank tick payloads.

Per TECHNICAL_PLAN §4.6 the on-disk tick format is versioned. A loader
that finds a tick file at version X older than the active registry's
major MUST run every migration step X → X+1 → ... → active before
consuming the payload. Migrations are forward-only — there is no
downgrade path; older agents that find a newer-major payload MUST refuse
to load.

Sprint_1 ships the chain skeleton with no actual migrations yet
(``CURRENT_VERSION == "1.0.0"`` is the genesis schema). When v1.1.0 etc.
add fields, append a (from_version, to_version, migrate_fn) tuple to
:data:`MIGRATIONS`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

# Active version this codebase emits. Bump in lockstep with
# .dev/contracts/_registry.json -> memory_bank_schema.version.
CURRENT_VERSION = "1.0.0"


def _identity(payload: dict[str, Any]) -> dict[str, Any]:
    """No-op upgrade. Used in places where shape didn't change but
    schema_version did (e.g. doc-only patch bump)."""
    return payload


# Ordered chain of upgrades. Sprint_1 has none — the genesis schema IS
# the active version. Sprint_2+ appends here as fields are introduced.
#
# Each entry: (from_version, to_version, upgrade_fn). The loader walks
# this list in order until ``schema_version`` matches CURRENT_VERSION.
MIGRATIONS: list[tuple[str, str, Callable[[dict[str, Any]], dict[str, Any]]]] = [
    # Example for sprint_2 reference (commented):
    # ("1.0.0", "1.1.0", _add_engine_columns),
]


def upgrade(payload: dict[str, Any]) -> dict[str, Any]:
    """Walk the migration chain until ``payload['schema_version']`` ==
    :data:`CURRENT_VERSION`. Raises :class:`ValueError` on:

    * a payload version newer than CURRENT_VERSION (forward-only rule)
    * a payload version that has no migration path forward (gap)
    """
    version = payload.get("schema_version")
    if version == CURRENT_VERSION:
        return payload

    if version is None:
        raise ValueError("tick payload missing schema_version field")

    # Forward-only check
    if _version_tuple(version) > _version_tuple(CURRENT_VERSION):
        raise ValueError(
            f"tick payload at {version} is newer than runtime {CURRENT_VERSION}; "
            f"forward-only migration policy applies (TECHNICAL_PLAN §4.6)"
        )

    # Walk the chain
    current = payload
    seen: set[str] = set()
    while current["schema_version"] != CURRENT_VERSION:
        v = current["schema_version"]
        if v in seen:
            raise ValueError(f"migration chain cycle detected at {v}")
        seen.add(v)
        step = next(
            ((src, dst, fn) for src, dst, fn in MIGRATIONS if src == v),
            None,
        )
        if step is None:
            raise ValueError(
                f"no migration path from {v} to {CURRENT_VERSION}; "
                f"chain gap (TECHNICAL_PLAN §4.6)"
            )
        _src, dst, fn = step
        current = fn(current)
        current["schema_version"] = dst
    return current


def _version_tuple(v: str) -> tuple[int, ...]:
    """Tuple parse for ordering comparisons. Pydantic / packaging would
    be overkill for the strict X.Y.Z surface used here."""
    return tuple(int(p) for p in v.split("."))
