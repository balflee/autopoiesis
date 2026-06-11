# Genesis — Track C Sim (`sim/`)

Layer 2 **calibration** framework for the Genesis Experiment.
Picks numerical values for the ~14 BREATH economic parameters
**before** any contracts deploy.

This is the **offline** track: zero dependency on the live chain,
zero dependency on the live Polymarket API, zero dependency on the
agent's LLM. The sim is the safety net that keeps the agent's
economy from being random when it hits real wallets.

## Status

**Sprint 1** — package skeleton only. Every engine module ships its
public surface as a stub raising `NotImplementedError`. Real Monte
Carlo + LHS + Bayesian-Optimization logic lands in sprint 2.

## Layout

| Module | Responsibility |
| --- | --- |
| `sim/params.py` | `ParamSpace` dataclass — the BREATH parameter schema. JSON round-trip is the cross-track contract for Track A (Solidity constants) and Track B (agent runtime defaults). |
| `sim/economy.py` | Pure-Python mirror of `EnergyController.sol`. Constructor only this sprint. |
| `sim/strategies.py` | Three archetype strategies — Pessimist, Optimist, Satisficer. Stub `decide()` raises until T-C-004. |
| `sim/market.py` | Historical Polymarket replay facade. Never hits live API. |
| `sim/runner.py` | Single-lifetime sim driver — `run_lifetime()` stub. |
| `sim/sweeper.py` | LHS + Bayesian-Optimization sweep — stubs. |
| `sim/objectives.py` | The 14 `GOOD_CALIBRATION` objectives. Sprint 1 returns `{name: False}` for the canonical key set. |
| `sim/analysis.py` | Reduces a finished sweep into the four artifact JSONs + `CALIBRATION_REPORT.md`. Stub. |
| `sim/replay.py` | Loads a Track B MemoryBank tarball into sim trajectory format. Signature locked today, body lands sprint 2. |
| `sim/cli.py` | `python -m sim.cli` entrypoint. `--help` works; subcommands land sprint 2. |

## Run

```bash
python -m sim.cli --help          # exits 0; prints usage
pytest -x tests/sim               # ≥3 tests, all green
mypy --strict sim/                # exits 0
```

## Output directory

Calibration runs land under `reports/calibration/<run_id>/` —
this sprint we ship the `.gitkeep` placeholder only.

## Spec anchors

* docs/PRD.md §14 — Calibration Framework + the 14 `GOOD_CALIBRATION`
  objectives.
* docs/PRD.md §14.1 — BREATH parameter table; `ParamSpace` enumerates
  five of these today (the ≥3 acceptance bar).
* docs/TECHNICAL_PLAN.md §4 — `sim/` module tree.
* docs/TECHNICAL_PLAN.md §4.6 — MemoryBank module API; `sim/replay.py` is
  one of four documented consumers.
* docs/DEV_FRAMEWORK.md §26 T1.2 / T2.7 / T2.8 — Track C calibration
  validator hard rules (3 archetypes mandatory, no look-ahead, CI-bounded
  early stopping).

## Cross-track interface contracts touched

None this sprint. Track C is a **consumer** of `memory_bank_schema`
(published by T-B-001); the JSON schema lives at
`agent/core/memory_bank_schema.json` and is referenced from the
`sim/replay.py` docstring only.
