import type {
  ApiErrorBody,
  BacktestConfig,
  CancelExperimentResponse,
  CreateBacktestResponse,
  CreateExperimentResponse,
  ExecutionSection,
  Experiment,
  IndicatorPreviewRequest,
  IndicatorPreviewResponse,
  IndicatorSpec,
  MetaResponse,
  PreflightRequest,
  PreflightResponse,
  StrategySection,
} from "./types";
import {
  normaliseBars,
  normaliseCompare,
  normaliseExecution,
  normaliseExperiments,
  normaliseIndicatorSpecs,
  normaliseInstrument,
  normaliseInstrumentList,
  normalisePresets,
  normaliseQuality,
  normaliseRun,
  normaliseRunList,
  normaliseSearch,
  normaliseStrategies,
} from "./normalise";

const API_BASE = "/api";

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly details?: unknown;

  constructor(status: number, body: ApiErrorBody) {
    super(body.message || "Request failed");
    this.name = "ApiError";
    this.code = body.code || `http_${status}`;
    this.status = status;
    this.details = body.details;
  }

  static async fromResponse(response: Response): Promise<ApiError> {
    let text = "";
    try {
      text = await response.text();
    } catch {
      // Body already consumed or unreadable; fall back to the status line.
    }
    let body: ApiErrorBody | null = null;
    if (text) {
      try {
        const parsed: unknown = JSON.parse(text);
        if (parsed && typeof parsed === "object") {
          const record = parsed as Record<string, unknown>;
          if (typeof record.code === "string" || typeof record.message === "string") {
            body = {
              code: typeof record.code === "string" ? record.code : `http_${response.status}`,
              message:
                typeof record.message === "string"
                  ? record.message
                  : response.statusText || "Request failed",
              details: record.details,
            };
          }
        }
      } catch {
        // Not JSON; use the status line below.
      }
    }
    return new ApiError(
      response.status,
      body ?? {
        code: `http_${response.status}`,
        message: text?.slice(0, 300) || response.statusText || "Request failed",
      },
    );
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

export function errorMessage(error: unknown): string {
  if (isApiError(error)) return `${error.message} (${error.code})`;
  if (error instanceof Error) return error.message;
  return "Unknown error";
}

function buildQuery(params: Record<string, string | number | boolean | null | undefined>): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    query.set(key, String(value));
  }
  const serialised = query.toString();
  return serialised ? `?${serialised}` : "";
}

interface RequestOptions {
  method?: "GET" | "POST" | "DELETE";
  body?: unknown;
  signal?: AbortSignal;
  headers?: Record<string, string>;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, signal, headers } = options;
  const init: RequestInit = { method, signal, headers: { Accept: "application/json", ...headers } };
  if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers = { ...init.headers, "Content-Type": "application/json" };
  }

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, init);
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, {
      code: "network_error",
      message: "The API is unreachable. Check that the backend is running on 127.0.0.1:8787.",
    });
  }

  if (!response.ok) throw await ApiError.fromResponse(response);
  if (response.status === 204) return undefined as T;

  const text = await response.text();
  if (!text) return undefined as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new ApiError(response.status, {
      code: "invalid_json",
      message: "The API returned a response that is not valid JSON.",
    });
  }
}

export interface BarsQuery {
  instrument_id: string;
  bar_type: string;
  from?: string;
  to?: string;
  limit?: number;
}

export interface InstrumentsQuery {
  league?: string;
  q?: string;
  limit?: number;
  offset?: number;
}

export const api = {
  health: (signal?: AbortSignal) =>
    request<{ status: string }>("/health", { signal }),

  meta: (signal?: AbortSignal) => request<MetaResponse>("/meta", { signal }),

  instruments: async (query: InstrumentsQuery = {}, signal?: AbortSignal) =>
    normaliseInstrumentList(
      await request<unknown>(
        `/instruments${buildQuery({
          league: query.league,
          q: query.q,
          limit: query.limit,
          offset: query.offset,
        })}`,
        { signal },
      ),
    ),

  instrument: async (instrumentId: string, signal?: AbortSignal) =>
    normaliseInstrument(
      await request<unknown>(`/instruments/${encodeURIComponent(instrumentId)}`, { signal }),
    ),

  quality: async (
    instrumentId: string,
    query: { from?: string; to?: string } = {},
    signal?: AbortSignal,
  ) =>
    normaliseQuality(
      await request<unknown>(
        `/instruments/${encodeURIComponent(instrumentId)}/quality${buildQuery(query)}`,
        { signal },
      ),
    ),

  search: async (q: string, signal?: AbortSignal) =>
    normaliseSearch(await request<unknown>(`/search${buildQuery({ q })}`, { signal })),

  bars: async (query: BarsQuery, signal?: AbortSignal) =>
    normaliseBars(await request<unknown>(`/bars${buildQuery({ ...query })}`, { signal })),

  indicators: async (signal?: AbortSignal) =>
    normaliseIndicatorSpecs(await request<unknown>("/indicators", { signal })),

  previewIndicator: (body: IndicatorPreviewRequest, signal?: AbortSignal) =>
    request<IndicatorPreviewResponse>("/indicators/preview", {
      method: "POST",
      body,
      signal,
    }),

  preflight: async (body: PreflightRequest, signal?: AbortSignal) => {
    const raw = await request<Record<string, unknown>>("/execution/preflight", {
      method: "POST",
      body,
      signal,
    });
    const execution = normaliseExecution(raw, raw);
    return {
      ...(raw as unknown as PreflightResponse),
      ...(execution ?? {}),
      warnings: Array.isArray(raw.warnings) ? (raw.warnings as string[]) : [],
    } as PreflightResponse;
  },

  strategies: async (signal?: AbortSignal) =>
    normaliseStrategies(await request<unknown>("/strategies", { signal })),

  saveStrategy: (body: { name: string; config: StrategySection }, signal?: AbortSignal) =>
    request<{ id: string; name: string }>("/strategies", { method: "POST", body, signal }),

  presets: async (signal?: AbortSignal) =>
    normalisePresets(await request<unknown>("/presets", { signal })),

  createPreset: (body: { name: string; config: BacktestConfig }, signal?: AbortSignal) =>
    request<{ id: string; name: string }>("/presets", { method: "POST", body, signal }),

  createBacktest: (body: BacktestConfig, signal?: AbortSignal) =>
    request<CreateBacktestResponse>("/backtests", { method: "POST", body, signal }),

  runs: async (
    query: { status?: string; limit?: number; offset?: number } = {},
    signal?: AbortSignal,
  ) => normaliseRunList(await request<unknown>(`/backtests${buildQuery(query)}`, { signal })),

  run: async (runId: string, signal?: AbortSignal) =>
    normaliseRun(
      await request<unknown>(`/backtests/${encodeURIComponent(runId)}`, { signal }),
    ),

  revealHoldout: (runId: string, signal?: AbortSignal) =>
    request<{
      reveal_count: number;
      variant_index?: number;
      warning?: boolean;
      message?: string;
      revealed_at?: string;
      revealed?: boolean;
    }>(`/backtests/${encodeURIComponent(runId)}/reveal-holdout`, {
      method: "POST",
      body: {},
      signal,
    }),

  compare: async (ids: string[], signal?: AbortSignal) =>
    normaliseCompare(
      await request<unknown>(`/runs/compare${buildQuery({ ids: ids.join(",") })}`, { signal }),
    ),

  artifact: (runId: string, name: string, signal?: AbortSignal) =>
    request<unknown>(
      `/backtests/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(name)}`,
      { signal },
    ),

  deleteRun: (runId: string, signal?: AbortSignal) =>
    request<void>(`/backtests/${encodeURIComponent(runId)}`, {
      method: "DELETE",
      signal,
    }),

  experiments: async (signal?: AbortSignal) =>
    normaliseExperiments(await request<unknown>("/experiments", { signal })),

  createExperiment: (body: unknown, signal?: AbortSignal) =>
    request<CreateExperimentResponse>("/experiments", { method: "POST", body, signal }),

  experiment: (id: string, signal?: AbortSignal) =>
    request<Experiment>(`/experiments/${encodeURIComponent(id)}`, { signal }),

  cancelExperiment: (id: string, signal?: AbortSignal) =>
    request<CancelExperimentResponse>(`/experiments/${encodeURIComponent(id)}/cancel`, {
      method: "POST",
      body: {},
      signal,
    }),

  eventsUrl: (runId: string) => `${API_BASE}/backtests/${encodeURIComponent(runId)}/events`,
};



export function indicatorList(
  response: IndicatorSpec[] | { indicators: IndicatorSpec[] } | undefined,
): IndicatorSpec[] {
  if (!response) return [];
  return Array.isArray(response) ? response : response.indicators ?? [];
}

export type { ExecutionSection };
