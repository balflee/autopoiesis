# syntax=docker/dockerfile:1.7
# =============================================================================
# Genesis Experiment — Agent control-plane Docker image (T-B-028)
# -----------------------------------------------------------------------------
# Two-stage build:
#   1. builder  — installs python deps into a venv at /opt/venv
#   2. runtime  — copies the venv + app source, runs as a non-root user
#
# Brief acceptance criteria (T-B-028):
#   * Python 3.11 base
#   * multi-stage build (builder + runtime)
#   * non-root user
#   * image < 500MB (python:3.11-slim + ~150MB of wheels)
#   * HEALTHCHECK hits /healthz on $PORT
#
# T-B-038 — three env vars now default to subdirs under /data so the
# Railway volume (declared in `railway.toml`) holds every piece of
# operator-mutable state:
#   * SANDBOX_STATE_DIR    = /data/sandbox        — JSONL streams + snapshot
#   * BACKTEST_OUTPUT_ROOT = /data/backtest/runs  — per-run results.json
#   * BACKTEST_CACHE_DIR   = /data/backtest/cache — seeded Polymarket cassettes
#
# Build:
#   docker build -f agent/server/Dockerfile -t genesis-agent:latest .
# Run (local smoke; Railway injects $PORT + secrets):
#   docker run --rm -p 8000:8000 \
#     -e PORT=8000 \
#     -e DASHBOARD_API_TOKEN=local-dev-not-a-real-secret \
#     -e GEMINI_API_KEY=$GEMINI_API_KEY \
#     -e SANDBOX_STATE_DIR=/data/sandbox \
#     -v $(pwd)/state:/data \
#     genesis-agent:latest
# =============================================================================

ARG PYTHON_VERSION=3.11

# ----------------------------------------------------------------------------- #
# Stage 1 — builder: install all production deps into an isolated venv.
# ----------------------------------------------------------------------------- #
FROM python:${PYTHON_VERSION}-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

# Build-only deps. We do NOT install gcc / build-essential in the final
# runtime stage — every Python dep in pyproject.toml ships a pure-Python
# or manylinux wheel for cpython 3.11 + linux/amd64. If a future dep
# breaks that assumption, add the build dep here and document it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Copy ONLY the dep manifest first so the layer cache survives unrelated
# source edits.
COPY pyproject.toml /build/pyproject.toml

# Isolated venv at /opt/venv — copied verbatim into the runtime stage.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

# Install the runtime deps. We extract the [project.dependencies] block
# from pyproject.toml via pip's PEP-517 path AND add the FastAPI server
# deps (fastapi + uvicorn[standard]) which are required by the deployed
# control plane but not by the dev-only test harness. Keeping them
# pinned here (rather than in pyproject.toml [project.dependencies])
# preserves the harness wheel's minimal surface for the orchestrator.
RUN pip install --upgrade pip \
    && pip install \
        "fastapi>=0.115,<1.0" \
        "uvicorn[standard]>=0.30,<1.0" \
        "pydantic>=2.9,<3.0" \
        "pyyaml>=6.0,<7.0" \
        "jsonschema>=4.23,<5.0" \
        "google-genai>=1.0.0,<2.0.0" \
        "numpy>=1.26,<3.0" \
        "pandas>=2.2,<3.0" \
        "pyarrow>=15.0,<20.0"

# ----------------------------------------------------------------------------- #
# Stage 2 — runtime: slim image, non-root user, app source + venv only.
# ----------------------------------------------------------------------------- #
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}" \
    PORT=8000 \
    SANDBOX_STATE_DIR=/data/sandbox \
    BACKTEST_OUTPUT_ROOT=/data/backtest/runs \
    BACKTEST_CACHE_DIR=/data/backtest/cache

# curl is REQUIRED by the HEALTHCHECK directive below; ca-certificates
# is needed for outbound HTTPS to AI Studio (Gemini). gosu drops the
# entrypoint from root → the non-root `agent` user AFTER it has chowned
# the Railway volume (see the CMD note below).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        gosu \
    && rm -rf /var/lib/apt/lists/*

# Pull in the venv built upstairs. /opt/venv is self-contained.
COPY --from=builder /opt/venv /opt/venv

# Non-root user — Brief requirement. UID 10001 stays out of the typical
# host-uid ranges so volume mounts from the operator's box don't collide.
RUN groupadd --system --gid 10001 agent \
    && useradd  --system --uid 10001 --gid agent --home-dir /home/agent --create-home agent \
    && mkdir -p /app /data/sandbox /data/backtest/runs /data/backtest/cache \
    && chown -R agent:agent /app /data

WORKDIR /app

# App source — only what the FastAPI control plane needs to import.
# We copy agent/ wholesale; the harness wheel is NOT installed here
# (the deployed agent doesn't run orchestrator tooling). Owning the
# /app tree before COPY guarantees the agent user can read every byte.
COPY --chown=agent:agent agent /app/agent
# data/ holds top-level ETL helpers that agent/engines/tennis_technical.py
# imports as `from data.etl.pit_correct import ...`. Without this COPY
# the runtime crashes at import-time with ModuleNotFoundError: 'data'.
# Surfaced on Railway deploy 2026-05-28 b756da3f startup failure.
COPY --chown=agent:agent data /app/data

# NOTE: we deliberately do NOT `USER agent` here. A Railway persistent
# volume mounts at /data owned by root:root at RUNTIME, OVERLAYING the
# build-time `chown -R agent:agent /data` above — so a process running as
# the non-root agent user gets EACCES on the first write under /data and
# uvicorn crashes before /healthz comes up (observed: 2026-06-19 deploy
# 89c99cd0 healthcheck failure). The container therefore starts as ROOT so
# the CMD can `chown /data` to the agent uid, then drops to `agent` via
# `gosu` for the actual server process. App code still runs non-root.

EXPOSE 8000

# Liveness probe — Railway also pings /healthz via the railway.toml
# healthcheck stanza, but the Docker-level HEALTHCHECK lets `docker ps`
# + local smoke runs surface the same signal without the platform.
# 30s interval, 5s timeout, 30s start-period (FastAPI cold-start is
# usually < 1s but uvicorn workers spin up async), 3 retries before
# unhealthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:${PORT}/healthz || exit 1

# uvicorn binds 0.0.0.0 so Railway's edge proxy can reach it. $PORT is
# injected by Railway at deploy time (default 8000 for local docker run).
# Single worker — the sandbox loop holds in-process state that doesn't
# survive a worker fork.
#
# Runs as ROOT (no `USER agent` above): first `chown -R agent:agent /data`
# so the root-owned Railway volume becomes writable by the agent uid, then
# `exec gosu agent uvicorn ...` drops to the non-root user for the server
# (gosu execs in place, so uvicorn is PID 1 and Railway's SIGTERM reaches
# it). NOTE: Railway overrides this CMD with railway.toml [deploy].startCommand
# in production — the two MUST stay in lockstep (both chown+gosu).
CMD ["sh", "-c", "chown -R agent:agent /data 2>/dev/null || true; exec gosu agent uvicorn agent.server.main:app --host 0.0.0.0 --port ${PORT}"]
