"""Death-aware survival metrics over season / groundhog artifacts.

The SINGLE home for the two distinct accessors (never cross them):

* :func:`journey_metric` reads the SERIALIZED journey dict returned by
  ``run_survival_over_rows`` (top-level ``lives`` LIST + ``summary.deaths``).
* :func:`groundhog_metric` reads the reincarnation ARTIFACT returned by
  ``run_groundhog_export`` (``incarnations`` LIST + ``gods_revenue`` /
  ``tithe_revenue`` — the full tithe+tribute economy).

All metrics are survival-class (death-rate / breath / vault), never PnL.
"""

from __future__ import annotations

from statistics import mean
from typing import Any

#: Each groundhog incarnation is seeded with this much USD bankroll (one life).
_SEED_BANKROLL_USD = 100.0


def journey_metric(journey: dict[str, Any], max_lives: int) -> dict[str, float]:
    """Death-aware metrics from a ``run_survival_over_rows`` journey dict.

    ``journey["lives"]`` is the per-life LIST (``summary["lives"]`` is just an
    int count). Breath is clamped+respawn-reset, so callers should read
    ``mean_final_breath`` alongside ``total_bets`` + ``death_rate``, never alone.
    """
    lives = journey["lives"]
    assert isinstance(lives, list), "journey['lives'] must be the per-life list"
    deaths = journey["summary"]["deaths"]
    return {
        "death_rate": deaths / max_lives if max_lives else 0.0,
        "mean_final_breath": (
            mean(life["final_breath"] for life in lives) if lives else 0.0
        ),
        "total_bets": float(sum(life["bets"] for life in lives)),
    }


def _curve_rise(progress: list[float]) -> float:
    """Mean of the last third minus the first third of a progress sequence.

    A robust "did the agent climb across lives" statistic that averages out
    per-life noise: ``0.0`` for a flat (non-learning) curve, ``>0`` when later
    incarnations reach further than earlier ones (self-evolution).
    """
    n = len(progress)
    if n < 2:
        return 0.0
    k = max(1, n // 3)
    return mean(progress[-k:]) - mean(progress[:k])


def learning_curve(artifact: dict[str, Any]) -> dict[str, Any]:
    """Per-incarnation learning curve from a groundhog reincarnation artifact.

    The 能学 (can-learn) demo metric: did the agent get FURTHER across successive
    lives? Returns the raw per-incarnation ``progress_pct`` / ``pnl_at_death`` /
    ``died`` sequences plus summary stats — ``rise`` (last-third minus first-third
    mean progress), ``best_progress_pct``, and whether/when it ``survived``. A
    non-learning (frozen) arm stays flat (``rise≈0``, never ``survived``); a
    learner's curve climbs.
    """
    incs = artifact.get("incarnations", [])
    progress = [float(inc["progress_pct"]) for inc in incs]
    return {
        "n_incarnations": len(incs),
        "progress_pct": progress,
        "pnl_at_death": [float(inc["pnl_at_death"]) for inc in incs],
        "died": [bool(inc["died"]) for inc in incs],
        "first_progress_pct": progress[0] if progress else 0.0,
        "final_progress_pct": progress[-1] if progress else 0.0,
        "best_progress_pct": max(progress) if progress else 0.0,
        "survived": bool(artifact.get("survived")),
        "surviving_incarnation": artifact.get("surviving_incarnation"),
        "rise": _curve_rise(progress),
    }


def aggregate_curves(curves: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-seed :func:`learning_curve` dicts into arm-level stats.

    The headline demo comparison is arm-vs-arm on these: ``survival_rate`` and
    ``mean_best_progress_pct`` separate a learner (climbs, often survives) from a
    frozen null (flat, rarely survives). ``mean_surviving_incarnation`` is over
    the seeds that survived (``None`` if none did).
    """
    n = len(curves)
    survived = [c for c in curves if c.get("survived")]
    return {
        "n_seeds": n,
        "survival_rate": len(survived) / n if n else 0.0,
        "mean_best_progress_pct": (
            mean(c["best_progress_pct"] for c in curves) if curves else 0.0
        ),
        "mean_final_progress_pct": (
            mean(c["final_progress_pct"] for c in curves) if curves else 0.0
        ),
        "mean_rise": mean(c["rise"] for c in curves) if curves else 0.0,
        "mean_surviving_incarnation": (
            mean(c["surviving_incarnation"] for c in survived)
            if survived
            else None
        ),
    }


def groundhog_metric(artifact: dict[str, Any]) -> dict[str, float]:
    """Death-aware metrics from a ``run_groundhog_export`` reincarnation artifact.

    Each incarnation is ONE life; ``death_rate`` is the fraction of incarnations
    that died. ``vault`` is the god's full take (deathbed tribute ``gods_revenue``
    + periodic rent ``tithe_revenue``); ``net_vs_seed`` subtracts the
    ``$100``/incarnation seed so a positive value means the economy made real
    money rather than recycling seed.
    """
    incs = artifact.get("incarnations", [])
    n = len(incs)
    deaths = sum(1 for inc in incs if inc.get("died"))
    vault = (artifact.get("gods_revenue") or 0.0) + (
        artifact.get("tithe_revenue") or 0.0
    )
    seed_injected = n * _SEED_BANKROLL_USD
    return {
        "death_rate": deaths / n if n else 0.0,
        "deaths": float(deaths),
        "n_incarnations": float(n),
        "survived": 1.0 if artifact.get("survived") else 0.0,
        "total_bets": float(sum(inc.get("bets", 0) for inc in incs)),
        "vault": float(vault),
        "seed_injected": float(seed_injected),
        "net_vs_seed": float(vault - seed_injected),
    }
