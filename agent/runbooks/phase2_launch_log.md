# Phase 2 Launch Log — Append-Only

> Each operator who runs the Phase 2 launch (real or smoke) appends a
> timestamped entry below. **Never edit prior entries.** A failed run
> gets its own entry with `status: failed` plus the failure mode.

Entry template:

```
## YYYY-MM-DDTHH:MM:SSZ — operator: <name> — status: <smoke|live|failed>

* trigger: <smoke | post-advancePhase live | rehearsal>
* commit: <sha>
* advancePhase tx hash: <0x… | n/a (smoke)>
* observed currentPhase() ordinal: <0|1|2 — must be 1 after advance>
* dashboard URL: <local | deployed>
* vitals at t=0: breath=<n> bankroll_usd=<n> gas_per_min=<n>
* llm_activated overlay fired: <yes|no>
* first decision logged: <NO_BET | BET …> tx=<0x…|fake>
* WS tape frame count: <n>
* WS tape distinct kinds: <…>
* notes: <free-form, ≤ 200 words>
```

---

## 2026-05-23T18:00:00Z — operator: T-B-008 (Track B agent) — status: smoke

* trigger: T-B-008 acceptance-criteria reproduction
* commit: HEAD of `task/T-B-008-phase2-launch-smoke` worktree
* advancePhase tx hash: n/a (smoke — `FakePhaseManagerReader` returns
  `PHASE_2_APPRENTICE` directly)
* observed currentPhase() ordinal: 1 (Apprenticeship)
* dashboard URL: not opened — smoke is a unit-level convergence check;
  the dashboard playback path consumes `data/fixtures/phase2_demo_tape.json`
  instead.
* vitals at t=0: breath=100.0 bankroll_usd=100.0 gas_per_min=0.5
* llm_activated overlay fired: yes — `memory_bank/observations/llm_activated.json`
  written; one WS frame emitted; idempotency proven by a second boot.
* first decision logged: NO_BET tx=`0xfake_0001` (route via low-confidence
  default signals — see `test_phase2_boot_records_first_decision_on_decision_log`).
* WS tape frame count (boot only): 7
* WS tape distinct kinds: decision, llm_activated, phase_transition,
  reflection, vitals, weights_updated
* WS demo tape (boot + curated arc) frame count: 30 (see
  `data/fixtures/phase2_demo_tape.json`)
* WS demo tape distinct kinds: 7 — adds `thought` on top of the boot's 6
* notes: hermetic — `FakeGeminiClient` rejects any call with an
  AssertionError; spy counters on chain reader / decision log / signal
  source all stay at 0 during `dry_run_plan()`. mypy --strict clean on
  agent/main.py + agent/runtime/ + agent/dashboard_bridge/.
  `pytest -x tests/agent/integration/test_phase2_launch_smoke.py` →
  **8 passed**. Full `pytest tests/agent/` → **143 passed**.

---

<!-- Next operator: append your entry above this marker, BELOW the
     previous one, oldest-first. -->
