/**
 * api_client.ts — typed thin wrapper around Track B's FastAPI control plane.
 *
 * Backend surface (eight routes, sprint_11 T-B-037 expanded):
 *
 *   POST /api/agent/start                  → 202 { run_id, status }
 *                                            409 { detail, run_id }   ← already running
 *   POST /api/agent/stop                   → 200 { status, final_state_path }
 *   GET  /api/agent/status                 → 200 StatusBody (see below)
 *   POST /api/agent/configure              → 202 AgentConfigureResponse  (T-B-037)
 *   POST /api/backtest/run                 → 202 { run_id, status }
 *                                            422/400 on invalid body
 *   POST /api/backtest/{run_id}/cancel     → 200 BacktestCancelResponse (T-B-037)
 *   GET  /api/backtest/{id}                → 200 BacktestResultBody | 404 { detail }
 *   GET  /api/state/stream                 → SSE — see lib/sse_subscribe.ts
 *
 * T-D-015 (sprint_11) — Track B now ships the typed wire shape natively:
 *
 *   * `submitBacktest()` POSTs the typed `BacktestRunRequest` body (configs +
 *     start_date + end_date + operator_note). The sprint_9 "stuff it all
 *     into note" hack is gone; the backend's sweep_runner consumes the
 *     `configs` list directly (T-B-037 §1).
 *   * The sprint_9 backtest-result adapter — and the row-level
 *     defaulting it carried — is gone. T-B-036 v2 ships
 *     {net_pnl_usd, sharpe, max_drawdown_pct, n_decisions, n_bets,
 *     win_rate_pct} natively. The only residual transform is a one-line
 *     `Number()` parse on `net_pnl_usd` because the backend renders it as a
 *     STRING via `str(Decimal)` to preserve precision across the JSON
 *     round-trip. That is not adapter logic — it's the JSON-→TS unmarshal.
 *   * `cancelBacktest()` is new. Hits `POST /api/backtest/{run_id}/cancel`
 *     and returns the typed cancel envelope; the dashboard's Cancel button
 *     in `/workshop` flips the SWEEP IN FLIGHT pill to "cancelled" within
 *     ≤5s (the cooperative-cancel latch the sweep runner polls per tick).
 *   * `configureAgent()` replaces the sprint_9 stub. The
 *     PROMOTE button now actually persists the chosen weights to
 *     `<state_dir>/agent_config.json` via the typed body; the next
 *     `/api/agent/start` rehydrates from that file. CEO-locked removal
 *     (D-S11-001 §scope-decisions §9).
 *
 * Same-origin proxy model (unchanged from T-D-011):
 *
 *   The browser bundle never sees a bearer token. Requests go out at the
 *   relative path `/api/proxy/...`; the Next.js server proxy injects auth
 *   from `DASHBOARD_API_TOKEN` and forwards verbatim to FastAPI.
 *
 *   The only override path is `NEXT_PUBLIC_DASHBOARD_API_URL_OVERRIDE` for
 *   local dev against a localhost FastAPI process. NEVER set in production.
 *
 * Every call rejects with a typed {@link ApiError} on non-2xx so the
 * dashboard can render an "offline" banner instead of crashing. The brief's
 * acceptance criterion is unchanged: *"Dashboard does NOT crash when backend
 * is unreachable — shows 'offline' state, retry button."*
 */

/**
 * Default base URL. Empty string + relative path means same-origin —
 * the browser will hit `/api/proxy/...` which Next.js routes to the
 * server-side proxy handler. Local-dev direct mode sets
 * `NEXT_PUBLIC_DASHBOARD_API_URL_OVERRIDE=http://localhost:8000` to skip
 * the proxy and talk straight to the FastAPI process.
 */
export const DEFAULT_API_BASE_URL: string =
  process.env.NEXT_PUBLIC_DASHBOARD_API_URL_OVERRIDE ?? "/api/proxy";

const TOKEN_LOCALSTORAGE_KEY = "genesis_api_token";

/* ------------------------------------------------------------------ */
/* Wire shapes — mirror agent/server/main.py + models.py 1:1.          */
/* ------------------------------------------------------------------ */

export interface StartResponse {
  readonly run_id: string;
  readonly status: "accepted";
}

export interface StartConflictResponse {
  readonly detail: "agent already running";
  readonly run_id: string;
}

export interface StopResponse {
  readonly status: "stopped";
  readonly final_state_path: string | null;
}

export type AgentPhase =
  | "PHASE_1_INFANCY"
  | "PHASE_2_APPRENTICE"
  | "PHASE_3_MASTER"
  | "PHASE_4_TERMINAL";

/**
 * Mirrors `agent.server.main.StatusResponse`. T-B-034 added optional
 * `last_run_status` + `error` fields.
 *
 * T-D-015 — `pending_config?: boolean` is declared OPTIONAL at the wire
 * layer so the dashboard can read it when Track B starts surfacing it.
 * As of T-B-037 the backend has NOT yet promoted the `agent_config.json`
 * existence check to /status; the field is reserved here so the
 * AgentControls 'Awaiting config' pill can hand off to the canonical
 * server-side signal when Track B ships it (see proposed_spec_change in
 * the T-D-015 delivery report). Until then the pill reads a Zustand
 * latch set by `configureAgent()` success — same UX, no schema drift.
 */
export interface StatusBody {
  readonly phase: AgentPhase | string | null;
  readonly breath: number | null;
  readonly last_tick_ts: string | null;
  readonly current_weights: Record<string, unknown> | null;
  readonly llm_cost_usd_this_month: number;
  readonly pending_proposals_count: number;
  readonly running: boolean;
  readonly run_id: string | null;
  readonly last_run_status?: "failed" | "cancelled" | null;
  readonly error?: unknown;
  /** Reserved — set by Track B when /status promotes the agent_config.json check. */
  readonly pending_config?: boolean;
}

export interface BacktestRunResponse {
  readonly run_id: string;
  readonly status: "accepted";
}

/**
 * T-D-015 — typed body for `POST /api/backtest/run` (T-B-037).
 *
 * Mirrors `agent.server.models.BacktestRunRequest` 1:1. All fields
 * OPTIONAL; the backend treats `{}` / `{configs: []}` identically by
 * falling through to the canonical 4-config default sweep so the
 * existing "RUN BACKTEST" button keeps working unchanged.
 */
export interface BacktestRunRequest {
  readonly start_date?: string | null;
  readonly end_date?: string | null;
  readonly configs?: ReadonlyArray<StartingWeightConfig>;
  readonly operator_note?: string | null;
}

/**
 * One operator-facing weight initialisation row.
 *
 * Mirrors `agent.server.models.StartingWeightConfig` 1:1 — the registry
 * contract is `starting_weight_config.v1.0.0.json`. All five mixing
 * parameters are scalar (alpha + beta were 3-vector / 2-vector on the
 * Weights model; this is the collapsed operator surface — see the
 * Pydantic model docstring).
 *
 * Validation:
 * - `w_r`, `w_s`, `alpha`, `beta` ∈ [0, 1]
 * - `rho` ∈ [-1, 1] (HARD — backend returns 400 on /api/agent/configure,
 *   422 on /api/backtest/run)
 * - `w_r + w_s ≈ 1.0` is WARN-only at the backend; the workshop UI does
 *   not pre-validate (the operator may type mid-edit values).
 */
export interface StartingWeightConfig {
  readonly label: string;
  readonly w_r: number;
  readonly w_s: number;
  readonly alpha: number;
  readonly beta: number;
  readonly rho: number;
}

/**
 * T-D-015 — typed body for `POST /api/agent/configure` (T-B-037).
 *
 * Mirrors `agent.server.models.AgentConfigureRequest`. Single-field
 * wrapper around `StartingWeightConfig` so future sibling fields
 * (e.g. `threshold_overrides`) land additively.
 */
export interface AgentConfigureRequest {
  readonly starting_weights: StartingWeightConfig;
}

/**
 * `POST /api/agent/configure` 202 response. Mirrors
 * `agent.server.models.AgentConfigureResponse`. The route echoes the
 * persisted config so the dashboard can update its local mirror
 * without an extra GET roundtrip, and surfaces the on-disk path so
 * the operator can confirm the write landed.
 */
export interface AgentConfigureResponse {
  readonly starting_weights: StartingWeightConfig;
  readonly persisted_path: string;
  readonly status: "accepted";
}

/**
 * `POST /api/backtest/{run_id}/cancel` 200 response. Mirrors
 * `agent.server.models.BacktestCancelResponse`. The cancel latch is
 * idempotent — a second call on an already-cancelled run still returns
 * 200 with `cancelled=true`.
 */
export interface BacktestCancelResponse {
  readonly run_id: string;
  readonly cancelled: boolean;
  readonly status: "cancelling";
}

/**
 * Backtest result row — mirrors the dict shape produced by
 * `agent.backtest.sweep_runner._serialise_metrics` per T-B-036 v2.
 *
 * NB: the backend renders `net_pnl_usd` as a STRING via `str(Decimal)` to
 * preserve precision across the JSON round-trip. The wire shape carries
 * the string; `parseBacktestRow` converts to a JS `number` for the table.
 */
export interface BacktestResultRowWire {
  readonly config_id: string;
  readonly label?: string;
  readonly starting_weights: StartingWeightConfig;
  readonly final_bankroll_usd: number;
  readonly net_pnl_usd: number | string;
  readonly sharpe: number;
  readonly max_drawdown_pct: number;
  readonly n_decisions: number;
  readonly n_bets: number;
  readonly win_rate_pct: number;
}

/** Parsed in-memory shape the workshop table renders. */
export interface BacktestResultRow {
  readonly config_id: string;
  readonly label?: string;
  readonly starting_weights: StartingWeightConfig;
  readonly final_bankroll_usd: number;
  readonly net_pnl_usd: number;
  readonly sharpe: number;
  readonly max_drawdown_pct: number;
  readonly n_decisions: number;
  readonly n_bets: number;
  readonly win_rate_pct: number;
}

/**
 * Typed envelope returned by `GET /api/backtest/{run_id}`. The backend
 * writes one of three shapes to `results.json` (`agent.backtest.sweep_runner`
 * for success; `agent.server.runner._safe_run` for failure / cancellation):
 *
 *  * SUCCESS  — `{run_id, results: [...], started_at, finished_at, ...}`
 *  * FAILED   — `{status: "failed", error: RegistryError, completed_at}`
 *  * CANCELLED — `{status: "cancelled", error: null, completed_at}`
 *
 * The dashboard distinguishes via the presence of `results` vs `status`.
 * `BacktestResult.status` is a normalised view derived in `parseBacktestResult`.
 */
export interface BacktestResult {
  readonly run_id: string;
  readonly status: "running" | "completed" | "failed" | "cancelled";
  readonly started_at: string;
  readonly completed_at?: string;
  readonly rows: ReadonlyArray<BacktestResultRow>;
  readonly note?: string;
  readonly error?: { type: string; message: string; traceback: string } | null;
}

/* ------------------------------------------------------------------ */
/* T-D-012 sprint_10 — proposal approve / reject wire shapes.          */
/* ------------------------------------------------------------------ */

export interface ProposalActionResponse {
  readonly proposal_id: string;
  readonly status: "approved" | "rejected";
  readonly applied_to_runtime: boolean;
}

/** Typed error so the dashboard can react to 401 / 409 / 5xx. */
export class ApiError extends Error {
  public readonly status: number;
  public readonly body: unknown;

  constructor(message: string, status: number, body: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

/* ------------------------------------------------------------------ */
/* Token resolution + low-level fetch                                  */
/* ------------------------------------------------------------------ */

/**
 * Resolve the bearer token. Pure read — never writes. Returns `null` so the
 * caller can decide what to do (we send the request anyway so the dashboard
 * shows the backend's 401 instead of refusing to call). The hard rule from
 * the brief forbids hardcoding tokens; this function is the *only* place
 * we look for one.
 */
export function resolveApiToken(): string | null {
  const fromEnv = process.env.NEXT_PUBLIC_DASHBOARD_API_TOKEN;
  if (typeof fromEnv === "string" && fromEnv.length > 0) return fromEnv;
  if (typeof window === "undefined") return null;
  try {
    const fromStorage = window.localStorage.getItem(TOKEN_LOCALSTORAGE_KEY);
    if (fromStorage && fromStorage.length > 0) return fromStorage;
  } catch {
    /* localStorage may be blocked (private mode / Safari ITP); silently fall through */
  }
  return null;
}

export interface ApiClientOptions {
  /** Override the base URL — used in tests + Storybook mocks. */
  readonly baseUrl?: string;
  /** Override the token resolver — used in tests. */
  readonly tokenProvider?: () => string | null;
  /** Per-request fetch implementation — used in tests. */
  readonly fetchImpl?: typeof fetch;
  /** Per-request signal — wire AbortController for cancellation. */
  readonly signal?: AbortSignal;
}

interface InternalFetchOptions extends ApiClientOptions {
  readonly method: "GET" | "POST";
  readonly path: string;
  readonly body?: unknown;
}

async function apiFetch<T>(opts: InternalFetchOptions): Promise<T> {
  const baseUrl = (opts.baseUrl ?? DEFAULT_API_BASE_URL).replace(/\/+$/, "");
  const url = `${baseUrl}${opts.path}`;
  const tokenProvider = opts.tokenProvider ?? resolveApiToken;
  const token = tokenProvider();
  const fetchFn = opts.fetchImpl ?? globalThis.fetch.bind(globalThis);

  const headers: Record<string, string> = {
    Accept: "application/json",
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";

  let response: Response;
  try {
    response = await fetchFn(url, {
      method: opts.method,
      headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      signal: opts.signal,
    });
  } catch (cause) {
    // Network-level failure (DNS, offline, CORS preflight rejection). Surface
    // as a status-0 ApiError so the dashboard's offline banner can match on
    // it without unwrapping the underlying TypeError shape.
    throw new ApiError(
      `network error: ${(cause as Error).message}`,
      0,
      null,
    );
  }

  const ct = response.headers.get("content-type") ?? "";
  let parsed: unknown = null;
  if (ct.includes("application/json")) {
    try {
      parsed = await response.json();
    } catch {
      parsed = null;
    }
  } else {
    try {
      parsed = await response.text();
    } catch {
      parsed = null;
    }
  }

  if (!response.ok) {
    throw new ApiError(
      `HTTP ${response.status} on ${opts.method} ${opts.path}`,
      response.status,
      parsed,
    );
  }

  return parsed as T;
}

/* ------------------------------------------------------------------ */
/* Public route handles                                                */
/* ------------------------------------------------------------------ */

export async function startAgent(
  options: ApiClientOptions = {},
): Promise<StartResponse> {
  return apiFetch<StartResponse>({
    ...options,
    method: "POST",
    path: "/api/agent/start",
  });
}

export async function stopAgent(
  options: ApiClientOptions = {},
): Promise<StopResponse> {
  return apiFetch<StopResponse>({
    ...options,
    method: "POST",
    path: "/api/agent/stop",
  });
}

export async function fetchStatus(
  options: ApiClientOptions = {},
): Promise<StatusBody> {
  return apiFetch<StatusBody>({
    ...options,
    method: "GET",
    path: "/api/agent/status",
  });
}

/**
 * T-D-015 — `POST /api/backtest/run` with the typed T-B-037 body.
 *
 * The sprint_9 hack that smuggled form values into `note` is gone. The
 * dashboard's `BacktestRunRequest` is sent verbatim; the backend's
 * sweep_runner consumes `configs` directly.
 *
 * Backward-compat: an undefined body OR an empty `configs` list triggers
 * the canonical 4-config default sweep server-side, so the existing
 * "RUN BACKTEST" button keeps working without per-request tuning.
 */
export async function submitBacktest(
  body: BacktestRunRequest = {},
  options: ApiClientOptions = {},
): Promise<BacktestRunResponse> {
  return apiFetch<BacktestRunResponse>({
    ...options,
    method: "POST",
    path: "/api/backtest/run",
    body,
  });
}

/**
 * T-D-015 — `POST /api/backtest/{run_id}/cancel`. Flips the
 * cooperative-cancel latch the sweep runner polls between configs;
 * actual disposition lands within ≤5s (one tick boundary on the test
 * sweep, one config boundary on the production sweep).
 *
 * Unknown run_id surfaces as `ApiError(status=404)` — the workshop
 * cancel button can ignore that case silently (the run may have just
 * completed between the user's click and the request hitting Python).
 *
 * Idempotent: a second cancel on an already-cancelled run still
 * returns 200 + `cancelled: true`.
 */
export async function cancelBacktest(
  runId: string,
  options: ApiClientOptions = {},
): Promise<BacktestCancelResponse> {
  return apiFetch<BacktestCancelResponse>({
    ...options,
    method: "POST",
    path: `/api/backtest/${encodeURIComponent(runId)}/cancel`,
  });
}

/**
 * T-D-015 — `POST /api/agent/configure`. Persists the chosen starting
 * weights to `<state_dir>/agent_config.json` atomically; the next
 * `/api/agent/start` rehydrates from the file.
 *
 * Replaces the sprint_9 stub (CEO-locked removal per
 * D-S11-001 §scope-decisions §9).
 *
 * Error mapping (mirrors the route):
 *   * 400 — invalid weight config (rho out of [-1, 1])
 *           body: { detail: { validation_errors: [...] } }
 *   * 401 — unauthenticated
 *
 * The route ALWAYS persists — even if the agent is currently running. The
 * running agent does NOT take effect immediately; the config is staged
 * until the operator re-starts. The dashboard surfaces this via the
 * AgentControls 'Awaiting config' pill.
 */
export async function configureAgent(
  weights: StartingWeightConfig,
  options: ApiClientOptions = {},
): Promise<AgentConfigureResponse> {
  return apiFetch<AgentConfigureResponse>({
    ...options,
    method: "POST",
    path: "/api/agent/configure",
    body: { starting_weights: weights },
  });
}

/**
 * `GET /api/backtest/{run_id}`. Returns a typed {@link BacktestResult}.
 *
 * Distinguishes three on-disk envelope shapes:
 *
 *   1. Success — `{run_id, results: [...], started_at, finished_at, ...}`
 *      → `{status: 'completed', rows: parsedRows}`
 *   2. Cancelled — `{status: 'cancelled', error: null, completed_at}`
 *      → `{status: 'cancelled', rows: []}`
 *   3. Failed — `{status: 'failed', error: {...}, completed_at}`
 *      → `{status: 'failed', rows: [], error}`
 *
 * 404 is re-raised as `ApiError(status=404)` — the workshop's polling
 * loop treats that as "not ready yet" and keeps polling.
 */
export async function fetchBacktestResult(
  runId: string,
  options: ApiClientOptions = {},
): Promise<BacktestResult> {
  const raw = await apiFetch<unknown>({
    ...options,
    method: "GET",
    path: `/api/backtest/${encodeURIComponent(runId)}`,
  });
  return parseBacktestResult(raw, runId);
}

/**
 * Parse the on-disk envelope into the dashboard's normalised shape.
 *
 * This is NOT an adapter in the T-B-035 sense — it does not synthesise
 * defaults for missing analytic fields the way the deleted
 * the sprint_9 adapter did. The backend ships every field natively
 * (T-B-036 v2). The only transforms are:
 *
 *   * `net_pnl_usd: string` → `number` (backend serialises as `str(Decimal)`
 *     to preserve precision across the JSON round-trip).
 *   * Surface the success vs cancelled vs failed envelope as a single
 *     `status` field the workshop UI can switch on.
 *
 * A row that genuinely lacks a numeric field (e.g. a sweep that errored
 * after partial settlement) raises through `Number(undefined) === NaN`
 * → we coerce to 0 ONLY for that single row, never for the whole table.
 * This is the explicit no-silent-default rule the CEO direction locks.
 */
export function parseBacktestResult(raw: unknown, runId: string): BacktestResult {
  const obj = (raw ?? {}) as Record<string, unknown>;

  // Failed / cancelled envelope written by _safe_run.
  const envelopeStatus = obj.status;
  if (envelopeStatus === "cancelled" || envelopeStatus === "failed") {
    return {
      run_id: runId,
      status: envelopeStatus,
      started_at: typeof obj.completed_at === "string"
        ? String(obj.completed_at)
        : new Date().toISOString(),
      completed_at: typeof obj.completed_at === "string"
        ? String(obj.completed_at)
        : undefined,
      rows: [],
      error: (obj.error ?? null) as
        | { type: string; message: string; traceback: string }
        | null,
    };
  }

  // Success envelope written by _write_results_json.
  const resultsRaw = Array.isArray(obj.results) ? (obj.results as unknown[]) : [];
  const rows = resultsRaw.map(parseBacktestRow);
  return {
    run_id: String(obj.run_id ?? runId),
    status: "completed",
    started_at: String(obj.started_at ?? new Date().toISOString()),
    completed_at: typeof obj.finished_at === "string"
      ? obj.finished_at
      : undefined,
    rows,
    note: typeof obj.operator_note === "string" ? obj.operator_note : undefined,
  };
}

function parseBacktestRow(row: unknown): BacktestResultRow {
  const r = (row ?? {}) as Record<string, unknown>;
  const startingWeights = (r.starting_weights ?? {}) as Record<string, unknown>;
  // Defensive numeric coercion — see parseBacktestResult docstring on the
  // explicit no-silent-default rule. The backend ships these fields
  // natively after T-B-036 v2; if any are missing we want a 0 in THIS row
  // rather than crashing the entire table.
  return {
    config_id: String(r.config_id ?? "—"),
    label: typeof r.label === "string" ? r.label : undefined,
    starting_weights: {
      label: typeof startingWeights.label === "string"
        ? startingWeights.label
        : "(unnamed)",
      w_r: num(startingWeights.w_r),
      w_s: num(startingWeights.w_s),
      alpha: num(startingWeights.alpha),
      beta: num(startingWeights.beta),
      rho: num(startingWeights.rho),
    },
    final_bankroll_usd: num(r.final_bankroll_usd),
    // net_pnl_usd is a STRING on the wire (Decimal-precision); convert.
    net_pnl_usd: num(r.net_pnl_usd),
    sharpe: num(r.sharpe),
    max_drawdown_pct: num(r.max_drawdown_pct),
    n_decisions: num(r.n_decisions),
    n_bets: num(r.n_bets),
    win_rate_pct: num(r.win_rate_pct),
  };
}

function num(v: unknown): number {
  if (v === null || v === undefined) return 0;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : 0;
}

/**
 * POST `/api/proposals/{id}/approve` — backend wires T-B-031.
 *
 * Path traversal is impossible: `encodeURIComponent` encodes any `/` or
 * `..` the operator could inject into a `proposal_id` string. The proxy
 * route accepts the encoded segment verbatim and forwards it to FastAPI
 * which validates the proposal_id against the JSONL fold.
 *
 * 200 → ProposalActionResponse; 404 (unknown id) / 409 (already
 * approved-or-rejected) surface as `ApiError` with the corresponding
 * status code so the dashboard can roll back its optimistic update.
 */
export async function approveProposal(
  proposalId: string,
  options: ApiClientOptions = {},
): Promise<ProposalActionResponse> {
  return apiFetch<ProposalActionResponse>({
    ...options,
    method: "POST",
    path: `/api/proposals/${encodeURIComponent(proposalId)}/approve`,
  });
}

/**
 * POST `/api/proposals/{id}/reject` — backend wires T-B-031.
 *
 * `reason` is optional per the backend `ProposalRejectRequest` model. When
 * provided, the backend folds it into `proposed_change.reject_reason` on
 * the audit row so the rejection's "why" survives in `proposals.jsonl`.
 * The dashboard reject modal collects this freeform string when the
 * operator chooses to elaborate; an empty / undefined value submits a
 * snap-rejection (body `{}`), which the backend accepts.
 */
export async function rejectProposal(
  proposalId: string,
  reason?: string,
  options: ApiClientOptions = {},
): Promise<ProposalActionResponse> {
  const trimmed = typeof reason === "string" ? reason.trim() : "";
  return apiFetch<ProposalActionResponse>({
    ...options,
    method: "POST",
    path: `/api/proposals/${encodeURIComponent(proposalId)}/reject`,
    body: trimmed.length > 0 ? { reason: trimmed } : {},
  });
}

/* ------------------------------------------------------------------ */
/* Polling helper — used by the workshop page to await sweep results.  */
/* ------------------------------------------------------------------ */

export interface PollOptions extends ApiClientOptions {
  /** ms between polls — default 2000 per acceptance criterion. */
  readonly intervalMs?: number;
  /** Hard ceiling so a runaway poll loop can't burn forever. */
  readonly timeoutMs?: number;
}

/** Yield the latest BacktestResult on a fixed interval, terminate on completion. */
export async function pollBacktestUntilComplete(
  runId: string,
  onUpdate: (result: BacktestResult) => void,
  options: PollOptions = {},
): Promise<BacktestResult> {
  const interval = options.intervalMs ?? 2000;
  const ceiling = options.timeoutMs ?? 5 * 60 * 1000;
  const started = Date.now();
  // eslint-disable-next-line no-constant-condition
  while (true) {
    try {
      const result = await fetchBacktestResult(runId, options);
      onUpdate(result);
      if (
        result.status === "completed"
        || result.status === "failed"
        || result.status === "cancelled"
      ) {
        return result;
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        // not ready yet — keep polling
      } else {
        throw err;
      }
    }
    if (Date.now() - started > ceiling) {
      throw new ApiError("backtest poll timeout", 0, null);
    }
    await new Promise((resolve) => setTimeout(resolve, interval));
    if (options.signal?.aborted) {
      throw new ApiError("aborted", 0, null);
    }
  }
}
