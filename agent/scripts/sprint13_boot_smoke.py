"""T-B-043 — sprint_13 SUBMISSION boot smoke.

Self-contained local smoke that proves the real :class:`SandboxPhase2Loop`
boots end-to-end against a freshly-deployed
``EnergyController + AgentLifecycle + TombstoneNFT`` stack on a local
anvil fork — and that the SSE stream carries the structural ``loop_boot``
marker (T-B-043) plus at least one real decision tick.

Flow (one rerun-safe pass):

1. Boot ``anvil --port 8545`` as a subprocess (chain_id 31337).
2. Deploy the contracts via ``forge script DeployAll`` against anvil,
   parsing the broadcast JSON for the three deployed addresses.
3. Export the 5 ``RH_CHAIN_*`` env vars + ``PROD_LOOP_CHAIN_ADAPTER_KIND=
   rh_chain`` + a hermetic ``SANDBOX_STATE_DIR`` so the real
   :func:`agent.server.main._build_chain_adapter` constructs a live
   :class:`RhChainAdapter` (T-B-042) against the anvil-deployed
   contracts.
4. Boot the FastAPI app programmatically via :mod:`uvicorn`
   (``agent.server.main:_build_default_app``) on port 8000.
5. POST ``/api/agent/start`` with a bearer token.
6. Tail ``/api/state/stream`` for up to 30 seconds, collecting events.
7. Assert:
   * Event #1 has ``kind == 'loop_boot'`` AND no ``placeholder`` key.
   * ≥ 1 event is a real decision (``kind in {'BET','NO_BET'}``).
   * Final :meth:`RhChainAdapter.read_breath` returns a finite float.
8. Stop the agent gracefully + tear down uvicorn + anvil.
9. Exit 0 on PASS, non-zero with the verdict on FAIL.

Rerun safety: every run uses a fresh tempdir for ``SANDBOX_STATE_DIR``
(no stale state from prior runs poisons event #1) and a brand-new anvil
instance (no stale chain state). Killing a stale anvil on port 8545
before boot keeps the developer machine clean across reruns.

Operator usage::

    # From repo root, with anvil + forge + python on PATH and
    # uvicorn[standard] installed:
    python agent/scripts/sprint13_boot_smoke.py

Exit code 0 ⇒ PASS (loop boots + decision tick captured + breath reads
back). Non-zero ⇒ FAIL with a structured verdict line on stdout.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Make the smoke rerun-safe regardless of cwd: running
# ``python agent/scripts/sprint13_boot_smoke.py`` puts the script's
# directory on sys.path, NOT the repo root, so a downstream
# ``from agent.runtime.rh_chain_adapter import ...`` would fail with
# ``ModuleNotFoundError: No module named 'agent'``. Prepending the repo
# root here makes the smoke invocation-agnostic. The uvicorn subprocess
# inherits this via the ``PYTHONPATH`` env var set in ``_set_environment``
# so its own ``agent.server.main:app`` import resolves too.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Anvil's deterministic account #0 — the default ``--mnemonic
# "test test test test test test test test test test test junk"`` seed.
# This key controls 10000 ETH on the local fork; we use it as both the
# deploy signer + the agent's settlement signer.
ANVIL_ACCOUNT_0_PRIVATE_KEY = (
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
)
ANVIL_ACCOUNT_0_ADDRESS = "0xf39Fd6e51aad88F6F4ce6aB8827279cfFFb92266"
ANVIL_PORT = 8545
ANVIL_RPC_URL = f"http://127.0.0.1:{ANVIL_PORT}"
ANVIL_CHAIN_ID = 31337

UVICORN_PORT = 8000
UVICORN_HOST = "127.0.0.1"
UVICORN_BASE_URL = f"http://{UVICORN_HOST}:{UVICORN_PORT}"

# Bearer used inside the smoke; matches DASHBOARD_API_TOKEN exported
# before uvicorn boot.
SMOKE_DASHBOARD_TOKEN = "t-b-043-smoke-" + secrets.token_hex(8)

# Drive the loop's per-tick wait down so the FIRST decision lands fast.
# 60 s / 600 = 100 ms — the SSE tail breaks early once the first decision
# event arrives, so this is a "fast first tick" knob, not a steady-state
# cadence (the SUBMISSION runtime ships at 1.0x compression). Brief lock:
# the same value the existing test_main_agent_start fixture uses.
PROD_LOOP_TIME_COMPRESSION_FOR_SMOKE = "600"
PROD_LOOP_TICK_INTERVAL_SECONDS_FOR_SMOKE = "60"

# How long to tail SSE before bailing. The decision tick should land
# inside ~3 s; 30 s gives a generous buffer for cold-start anvil + forge.
SSE_TAIL_BUDGET_SECONDS = 30.0
HEALTHZ_WAIT_BUDGET_SECONDS = 30.0
ANVIL_BOOT_BUDGET_SECONDS = 15.0


def _print_section(title: str) -> None:
    """Cheap section divider so the captured log is scannable."""
    print(f"\n=== {title} ===", flush=True)


def _port_is_listening(host: str, port: int, timeout: float = 0.5) -> bool:
    """True iff ``host:port`` accepts a TCP connect inside ``timeout``."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _kill_existing_anvil(port: int) -> None:
    """Best-effort cleanup of any anvil already bound to ``port``.

    Idempotent: silent if no listener exists. Keeps the smoke rerun-safe
    against a developer machine that left an anvil running from a
    previous attempt.
    """
    if not _port_is_listening("127.0.0.1", port, timeout=0.2):
        return
    print(f"  port {port} already in use — attempting to free it", flush=True)
    # Cross-platform kill. On Windows we use taskkill against the anvil
    # process name; on POSIX, pkill suffices. Both are best-effort.
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/IM", "anvil.exe"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        subprocess.run(
            ["pkill", "-f", "anvil"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    # Give the OS a moment to release the socket.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if not _port_is_listening("127.0.0.1", port, timeout=0.2):
            return
        time.sleep(0.2)


def _start_anvil(repo_root: Path) -> subprocess.Popen[bytes]:
    """Start anvil on :data:`ANVIL_PORT` and block until it accepts conns.

    Anvil's stdout is suppressed (the smoke captures the boot proof via
    /healthz + SSE, not via anvil logs). The Popen handle is returned
    so the caller can ``terminate()`` it during teardown.
    """
    _print_section("anvil boot")
    _kill_existing_anvil(ANVIL_PORT)
    # ``--silent`` removes the noisy "eth_chainId" log per request; the
    # deterministic mnemonic keeps account 0 stable across runs.
    proc = subprocess.Popen(
        [
            "anvil",
            "--port",
            str(ANVIL_PORT),
            "--chain-id",
            str(ANVIL_CHAIN_ID),
            "--silent",
        ],
        cwd=str(repo_root),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + ANVIL_BOOT_BUDGET_SECONDS
    while time.monotonic() < deadline:
        if _port_is_listening("127.0.0.1", ANVIL_PORT, timeout=0.5):
            print(f"  anvil up on {ANVIL_RPC_URL} (pid={proc.pid})", flush=True)
            return proc
        if proc.poll() is not None:
            raise RuntimeError(
                f"anvil exited prematurely (code={proc.returncode}) before "
                f"accepting connections on port {ANVIL_PORT}"
            )
        time.sleep(0.3)
    proc.terminate()
    raise RuntimeError(
        f"anvil did not bind to {ANVIL_PORT} within "
        f"{ANVIL_BOOT_BUDGET_SECONDS}s"
    )


def _run_deploy_all(repo_root: Path) -> dict[str, str]:
    """Run ``forge script DeployAll`` and parse the three deployed addresses.

    Returns a dict keyed by the three contract names the
    :class:`RhChainAdapter` env vars consume:
      * ``EnergyController``
      * ``AgentLifecycle``
      * ``TombstoneNFT``
    """
    _print_section("forge script DeployAll")
    env = os.environ.copy()
    # ATTESTATION_SIGNER is the off-chain signer the EnergyController
    # validates EIP-712 attestations against. We point it at the same
    # account the agent will sign with, so the smoke's settlement path
    # round-trips against the same key.
    env["ATTESTATION_SIGNER"] = ANVIL_ACCOUNT_0_ADDRESS
    cmd = [
        "forge",
        "script",
        "script/DeployAll.s.sol:DeployAll",
        "--rpc-url",
        ANVIL_RPC_URL,
        "--private-key",
        ANVIL_ACCOUNT_0_PRIVATE_KEY,
        "--broadcast",
        "--silent",
    ]
    result = subprocess.run(
        cmd,
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if result.returncode != 0:
        print(result.stdout, flush=True)
        print(result.stderr, file=sys.stderr, flush=True)
        raise RuntimeError(
            f"forge script DeployAll failed (exit={result.returncode})"
        )

    # Broadcast log lives at broadcast/<script>/<chain_id>/run-latest.json
    # — the canonical foundry path Forge writes deploy receipts to.
    broadcast_path = (
        repo_root
        / "broadcast"
        / "DeployAll.s.sol"
        / str(ANVIL_CHAIN_ID)
        / "run-latest.json"
    )
    if not broadcast_path.exists():
        raise RuntimeError(
            f"forge script succeeded but broadcast log missing at "
            f"{broadcast_path}"
        )
    broadcast = json.loads(broadcast_path.read_text(encoding="utf-8"))

    addresses: dict[str, str] = {}
    for tx in broadcast.get("transactions", []):
        # ``CREATE`` transactions carry contractName + contractAddress.
        if tx.get("transactionType") == "CREATE":
            name = tx.get("contractName")
            addr = tx.get("contractAddress")
            if isinstance(name, str) and isinstance(addr, str):
                addresses[name] = addr
    required = ("EnergyController", "AgentLifecycle", "TombstoneNFT")
    missing = [n for n in required if n not in addresses]
    if missing:
        raise RuntimeError(
            f"DeployAll broadcast missing addresses for: {missing!r} — "
            f"got {sorted(addresses.keys())!r}"
        )
    # web3.py rejects lower-case addresses (it requires the EIP-55
    # mixed-case checksum). The broadcast log emits lower-case strings;
    # checksum them once here so every downstream consumer (env var,
    # contract handle, signature) sees the canonical shape.
    from eth_utils import to_checksum_address  # type: ignore[attr-defined]
    checksummed: dict[str, str] = {
        name: str(to_checksum_address(addresses[name])) for name in required
    }
    for name in required:
        print(f"  {name:<20s} {checksummed[name]}", flush=True)
    return checksummed


def _start_uvicorn(state_dir: Path) -> subprocess.Popen[bytes]:
    """Boot the FastAPI app via uvicorn on :data:`UVICORN_PORT`.

    The subprocess inherits the parent's env vars (already populated
    with the 5 ``RH_CHAIN_*`` + ``PROD_LOOP_CHAIN_ADAPTER_KIND`` +
    state-path knobs). uvicorn's standard import path
    ``agent.server.main:app`` triggers
    :func:`_build_default_app` on import, which wires the
    :class:`RhChainAdapter` against the configured env.
    """
    _print_section("uvicorn boot (agent.server.main:app)")
    # uvicorn's autobuild runs ``_build_default_app`` which mkdir's the
    # sandbox state dir, but that happens AFTER the log file path is
    # already pinned. mkdir up-front so the log handle opens cleanly.
    state_dir.mkdir(parents=True, exist_ok=True)
    log_path = state_dir / "uvicorn.log"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "agent.server.main:app",
            "--host",
            UVICORN_HOST,
            "--port",
            str(UVICORN_PORT),
            "--log-level",
            "warning",
        ],
        stdout=log_path.open("wb"),
        stderr=subprocess.STDOUT,
    )
    # Wait for /healthz to return 200.
    import httpx  # local import — keeps top-of-file imports light.
    deadline = time.monotonic() + HEALTHZ_WAIT_BUDGET_SECONDS
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            # Surface the uvicorn log so the operator can diagnose.
            log_tail = log_path.read_text(encoding="utf-8", errors="replace")
            raise RuntimeError(
                f"uvicorn exited prematurely (code={proc.returncode}) "
                f"before /healthz responded. Tail:\n{log_tail[-2000:]}"
            )
        try:
            r = httpx.get(f"{UVICORN_BASE_URL}/healthz", timeout=1.0)
            if r.status_code == 200:
                print(f"  uvicorn up on {UVICORN_BASE_URL} (pid={proc.pid})",
                      flush=True)
                return proc
        except httpx.HTTPError:
            pass
        time.sleep(0.4)
    proc.terminate()
    raise RuntimeError(
        f"uvicorn did not answer /healthz inside "
        f"{HEALTHZ_WAIT_BUDGET_SECONDS}s"
    )


async def _post_start(auth_headers: dict[str, str]) -> dict[str, Any]:
    """POST /api/agent/start — returns the response body on 202."""
    import httpx
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(
            f"{UVICORN_BASE_URL}/api/agent/start",
            headers=auth_headers,
        )
    if r.status_code != 202:
        raise RuntimeError(
            f"/api/agent/start expected 202; got {r.status_code}: {r.text}"
        )
    body: dict[str, Any] = r.json()
    print(f"  /api/agent/start -> 202, run_id={body.get('run_id')}", flush=True)
    return body


async def _post_stop(auth_headers: dict[str, str]) -> None:
    """POST /api/agent/stop — best-effort, swallows non-fatal errors."""
    import httpx
    with contextlib.suppress(Exception):
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                f"{UVICORN_BASE_URL}/api/agent/stop",
                headers=auth_headers,
            )
        print(f"  /api/agent/stop -> {r.status_code}", flush=True)


async def _tail_sse(
    *, auth_headers: dict[str, str], budget_seconds: float
) -> list[dict[str, Any]]:
    """Tail /api/state/stream until the smoke invariants are observable.

    Stops early once BOTH (a) event #1 is on hand AND (b) at least one
    real decision event (``kind in {BET, NO_BET}``) has been seen.
    ``budget_seconds`` is the OUTER bound so a regression that never
    emits a decision still terminates the smoke cleanly.

    Each SSE frame is ``event: <stream_name>\\ndata: <json>\\n\\n``; we
    capture both fields so the smoke can prove the wire shape, not just
    the JSON payload.
    """
    import httpx
    events: list[dict[str, Any]] = []
    deadline = time.monotonic() + budget_seconds
    timeout = httpx.Timeout(
        connect=5.0, read=budget_seconds + 5.0,
        write=5.0, pool=5.0,
    )
    decision_kinds = {"BET", "NO_BET"}
    decision_seen = False
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream(
            "GET",
            f"{UVICORN_BASE_URL}/api/state/stream",
            headers=auth_headers,
        ) as response:
            if response.status_code != 200:
                raise RuntimeError(
                    f"/api/state/stream expected 200; got "
                    f"{response.status_code}"
                )
            event_name: str | None = None
            async for raw_line in response.aiter_lines():
                if time.monotonic() >= deadline:
                    break
                line = raw_line.rstrip("\r")
                if line.startswith("event:"):
                    event_name = line[len("event:"):].strip()
                elif line.startswith("data:"):
                    data_str = line[len("data:"):].strip()
                    try:
                        parsed = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    events.append({
                        "event": event_name or "",
                        "data": parsed,
                    })
                    event_name = None
                    if (
                        isinstance(parsed, dict)
                        and parsed.get("kind") in decision_kinds
                    ):
                        decision_seen = True
                    # Stop as soon as we have the boot row + ≥1 decision.
                    # Saves wall-clock + keeps the captured log readable.
                    if len(events) >= 1 and decision_seen:
                        break
                # Blank line = end of frame; just keep going.
    return events


async def _read_breath_from_chain() -> float:
    """Call the live :class:`RhChainAdapter` against anvil to fetch breath.

    Final structural assertion: after the loop has booted + ticked, the
    chain-side BREATH value MUST be a finite float (anvil hasn't been
    torn down yet; the EnergyController initialized with INITIAL_BREATH
    so the call returns the seed value when no settlements have landed).
    """
    from agent.runtime.rh_chain_adapter import build_from_env
    adapter = build_from_env()
    try:
        value = await adapter.read_breath()
    finally:
        # No persistent connections to close — adapter is stateless
        # except for the cached nonce set; nothing to teardown.
        pass
    return float(value)


def _terminate_process(proc: subprocess.Popen[bytes], *, label: str) -> None:
    """Send SIGTERM (or Windows-friendly terminate), wait briefly, then kill."""
    if proc.poll() is not None:
        return
    print(f"  terminating {label} (pid={proc.pid})", flush=True)
    with contextlib.suppress(Exception):
        proc.terminate()
    try:
        proc.wait(timeout=5.0)
        return
    except subprocess.TimeoutExpired:
        pass
    with contextlib.suppress(Exception):
        proc.kill()
        proc.wait(timeout=3.0)


def _format_event_brief(event: dict[str, Any]) -> str:
    """One-line summary of an SSE event for the stdout log."""
    name = event.get("event", "?")
    data = event.get("data", {})
    if isinstance(data, dict):
        kind = data.get("kind", "?")
        tick = data.get("tick")
        extras: list[str] = []
        if tick is not None:
            extras.append(f"tick={tick}")
        if data.get("loop") is not None:
            extras.append(f"loop={data['loop']}")
        if data.get("no_bet_reason") is not None:
            extras.append(f"reason={data['no_bet_reason']}")
        if data.get("placeholder") is True:
            extras.append("placeholder=TRUE")
        extras_str = " ".join(extras)
        return f"event={name:<11s} kind={kind:<12s} {extras_str}".rstrip()
    return f"event={name} data={data!r}"


def _print_event_log(events: list[dict[str, Any]]) -> None:
    _print_section(f"SSE event log ({len(events)} events captured)")
    if not events:
        print("  (no events)", flush=True)
        return
    for i, ev in enumerate(events, start=1):
        print(f"  #{i:>3d}  {_format_event_brief(ev)}", flush=True)


def _assert_smoke(
    events: list[dict[str, Any]], breath_value: float
) -> tuple[bool, str]:
    """Apply the brief-locked assertions; returns ``(passed, verdict_line)``.

    Acceptance criteria (T-B-043):
      * event #1 has ``kind=='loop_boot'`` AND no ``placeholder`` key
      * ≥ 1 event represents a real decision tick (``kind`` in
        ``{'BET','NO_BET'}``)
      * final :meth:`read_breath` returns a finite float
    """
    import math
    failures: list[str] = []

    if not events:
        failures.append("no SSE events captured")
    else:
        first = events[0].get("data", {})
        if first.get("kind") != "loop_boot":
            failures.append(
                f"event #1 kind={first.get('kind')!r} (expected 'loop_boot')"
            )
        if "placeholder" in first:
            failures.append(
                f"event #1 carries 'placeholder' key (value="
                f"{first.get('placeholder')!r}) — placeholder loop survived"
            )

    decision_kinds = {"BET", "NO_BET"}
    decision_events = [
        ev for ev in events
        if isinstance(ev.get("data"), dict)
        and ev["data"].get("kind") in decision_kinds
    ]
    if not decision_events:
        failures.append(
            "no decision event seen (need ≥1 with kind in {BET,NO_BET})"
        )

    if not math.isfinite(breath_value):
        failures.append(f"read_breath returned non-finite {breath_value!r}")

    if failures:
        return False, (
            "FAIL — " + "; ".join(failures)
        )
    return True, (
        f"PASS — real loop boots, decision ticks land on SSE, "
        f"chain-side BREATH reads back finite "
        f"({breath_value:.4f} USD)"
    )


async def _run_smoke_async(
    auth_headers: dict[str, str],
) -> tuple[list[dict[str, Any]], float]:
    """Async core: start agent, tail SSE, read breath, stop agent."""
    await _post_start(auth_headers)
    try:
        _print_section(f"tailing /api/state/stream for {SSE_TAIL_BUDGET_SECONDS}s")
        events = await _tail_sse(
            auth_headers=auth_headers,
            budget_seconds=SSE_TAIL_BUDGET_SECONDS,
        )
        _print_section("reading chain-side BREATH via RhChainAdapter")
        breath = await _read_breath_from_chain()
        print(f"  read_breath() -> {breath:.6f} USD", flush=True)
    finally:
        _print_section("stopping agent")
        await _post_stop(auth_headers)
    return events, breath


def _set_environment(
    *, addresses: dict[str, str], state_dir: Path
) -> dict[str, str]:
    """Mutate ``os.environ`` with every env var the smoke needs.

    Returns a copy of just the smoke-set keys for the captured log
    (so the operator can audit what was injected without dumping the
    full environ).
    """
    smoke_env = {
        # 5 RH_CHAIN_* — consumed by RhChainAdapter.build_from_env.
        "RH_CHAIN_RPC_URL": ANVIL_RPC_URL,
        "RH_CHAIN_ENERGY_CONTROLLER_ADDRESS": addresses["EnergyController"],
        "RH_CHAIN_AGENT_LIFECYCLE_ADDRESS": addresses["AgentLifecycle"],
        "RH_CHAIN_TOMBSTONE_NFT_ADDRESS": addresses["TombstoneNFT"],
        "RH_CHAIN_SIGNER_PRIVATE_KEY": ANVIL_ACCOUNT_0_PRIVATE_KEY,
        # T-B-041 — switch the prod loop's chain adapter to the real one.
        "PROD_LOOP_CHAIN_ADAPTER_KIND": "rh_chain",
        # Compressed cadence so a tick lands in the SSE budget.
        "PROD_LOOP_TIME_COMPRESSION": PROD_LOOP_TIME_COMPRESSION_FOR_SMOKE,
        "PROD_LOOP_TICK_INTERVAL_SECONDS":
            PROD_LOOP_TICK_INTERVAL_SECONDS_FOR_SMOKE,
        # Hermetic state — every run gets a fresh dir.
        "SANDBOX_STATE_DIR": str(state_dir),
        "BACKTEST_OUTPUT_ROOT": str(state_dir / "backtest" / "runs"),
        "BACKTEST_CACHE_DIR": str(state_dir / "backtest" / "cache"),
        # FastAPI bearer.
        "DASHBOARD_API_TOKEN": SMOKE_DASHBOARD_TOKEN,
        # Force uvicorn-side autobuild path on import (the conftest sets
        # this to "0" for the test suite; the smoke wants the prod app).
        "GENESIS_SERVER_AUTOBUILD": "1",
        # PYTHONPATH carries the repo root into the uvicorn subprocess
        # so its ``agent.server.main:app`` import resolves regardless of
        # the user's cwd at smoke launch time. Preserves any pre-existing
        # PYTHONPATH the operator may have set (semicolon on Windows,
        # colon elsewhere — ``os.pathsep`` keeps the smoke cross-platform).
        "PYTHONPATH": (
            str(_REPO_ROOT)
            if not os.environ.get("PYTHONPATH")
            else str(_REPO_ROOT) + os.pathsep + os.environ["PYTHONPATH"]
        ),
    }
    os.environ.update(smoke_env)
    return smoke_env


def main() -> int:
    """Drive the smoke; return process exit code (0 = PASS)."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    smoke_started_iso = datetime.now(UTC).isoformat()

    print(f"=== T-B-043 sprint_13 boot smoke @ {smoke_started_iso} ===",
          flush=True)
    print(f"  repo_root: {repo_root}", flush=True)

    anvil_proc: subprocess.Popen[bytes] | None = None
    uvicorn_proc: subprocess.Popen[bytes] | None = None
    tmpdir_obj: tempfile.TemporaryDirectory[str] | None = None
    exit_code = 1
    verdict_line = "FAIL — smoke did not reach the assert step"

    try:
        # 1. anvil
        anvil_proc = _start_anvil(repo_root)

        # 2. deploy contracts
        addresses = _run_deploy_all(repo_root)

        # 3. env vars + hermetic state dir
        tmpdir_obj = tempfile.TemporaryDirectory(prefix="t-b-043-")
        state_dir = Path(tmpdir_obj.name) / "sandbox"
        smoke_env = _set_environment(addresses=addresses, state_dir=state_dir)
        _print_section("env vars (smoke-injected)")
        for k, v in sorted(smoke_env.items()):
            shown = v
            # Don't echo the full private key — first 10 chars is enough
            # to prove the value is set without leaking the seed.
            if k.endswith("PRIVATE_KEY") and len(v) > 20:
                shown = v[:10] + "..." + v[-4:]
            elif k == "DASHBOARD_API_TOKEN" and len(v) > 20:
                shown = v[:10] + "..."
            print(f"  {k}={shown}", flush=True)

        # 4. boot uvicorn
        uvicorn_proc = _start_uvicorn(state_dir)

        # 5-7. start agent, tail SSE, read breath, stop agent
        auth_headers = {"Authorization": f"Bearer {SMOKE_DASHBOARD_TOKEN}"}
        events, breath = asyncio.run(_run_smoke_async(auth_headers))

        # 8. print the event log + run assertions
        _print_event_log(events)
        passed, verdict_line = _assert_smoke(events, breath)
        exit_code = 0 if passed else 2

    except Exception as exc:
        import traceback
        verdict_line = f"FAIL — {type(exc).__name__}: {exc}"
        exit_code = 3
        _print_section("traceback")
        traceback.print_exc(file=sys.stdout)
        if uvicorn_proc is not None and tmpdir_obj is not None:
            log_path = Path(tmpdir_obj.name) / "sandbox" / "uvicorn.log"
            if log_path.exists():
                _print_section("uvicorn.log tail (last 80 lines)")
                lines = log_path.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
                for line in lines[-80:]:
                    print(line, flush=True)

    finally:
        # 9. teardown — always, even on early failure.
        _print_section("teardown")
        if uvicorn_proc is not None:
            _terminate_process(uvicorn_proc, label="uvicorn")
        if anvil_proc is not None:
            _terminate_process(anvil_proc, label="anvil")
        if tmpdir_obj is not None:
            with contextlib.suppress(Exception):
                tmpdir_obj.cleanup()

    # The verdict line is the LAST thing on stdout so the captured log
    # can grep for it from the bottom — matches the "first line of
    # boot_smoke_log.md is the verdict" inversion (the log writer flips
    # this to be the FIRST line).
    print(f"\n=== VERDICT ===\n{verdict_line}", flush=True)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
