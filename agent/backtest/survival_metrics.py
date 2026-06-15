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
