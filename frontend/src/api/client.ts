import type {
  ApiErrorBody,
  BacktestConfig,
  BarsResponse,
  CompareResponse,
  CreateBacktestResponse,
  ExecutionSection,
  Experiment,
  ExperimentListResponse,
  IndicatorPreviewRequest,
  IndicatorPreviewResponse,
  IndicatorSpec,
  Instrument,
  InstrumentListResponse,
  InstrumentQuality,
  MetaResponse,
  PreflightRequest,
  PreflightResponse,
  Preset,
  PresetListResponse,
  RunDetail,
  RunListResponse,
  RunSummary,
  SavedStrategy,
  SearchResponse,
  StrategiesResponse,
  StrategySection,
} from "./types";

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

function normaliseStrategies(raw: unknown): StrategiesResponse {
  if (Array.isArray(raw)) {
    const builtins = raw.filter(
      (item): item is Record<string, unknown> =>
        !!item && typeof item === "object" && "id" in item && !("strategy" in item),
    );
    const saved = raw.filter(
      (item): item is Record<string, unknown> =>
        !!item && typeof item === "object" && "strategy" in item,
    );
    return {
      builtins: builtins as unknown as StrategiesResponse["builtins"],
      saved: saved as unknown as SavedStrategy[],
    };
  }
  const record = (raw ?? {}) as Partial<StrategiesResponse>;
  return {
    builtins: Array.isArray(record.builtins)
      ? record.builtins
      : ((record as { strategies?: StrategiesResponse["builtins"] }).strategies ?? []),
    saved: Array.isArray(record.saved) ? record.saved : [],
  };
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

  instruments: (query: InstrumentsQuery = {}, signal?: AbortSignal) =>
    request<InstrumentListResponse>(
      `/instruments${buildQuery({
        league: query.league,
        q: query.q,
        limit: query.limit,
        offset: query.offset,
      })}`,
      { signal },
    ),

  instrument: (instrumentId: string, signal?: AbortSignal) =>
    request<Instrument>(`/instruments/${encodeURIComponent(instrumentId)}`, { signal }),

  quality: (
    instrumentId: string,
    query: { from?: string; to?: string } = {},
    signal?: AbortSignal,
  ) =>
    request<InstrumentQuality>(
      `/instruments/${encodeURIComponent(instrumentId)}/quality${buildQuery(query)}`,
      { signal },
    ),

  search: (q: string, signal?: AbortSignal) =>
    request<SearchResponse>(`/search${buildQuery({ q })}`, { signal }),

  bars: (query: BarsQuery, signal?: AbortSignal) =>
    request<BarsResponse>(`/bars${buildQuery({ ...query })}`, { signal }),

  indicators: (signal?: AbortSignal) =>
    request<IndicatorSpec[] | { indicators: IndicatorSpec[] }>("/indicators", { signal }),

  previewIndicator: (body: IndicatorPreviewRequest, signal?: AbortSignal) =>
    request<IndicatorPreviewResponse>("/indicators/preview", {
      method: "POST",
      body,
      signal,
    }),

  preflight: (body: PreflightRequest, signal?: AbortSignal) =>
    request<PreflightResponse>("/execution/preflight", { method: "POST", body, signal }),

  strategies: async (signal?: AbortSignal) =>
    normaliseStrategies(await request<unknown>("/strategies", { signal })),

  saveStrategy: (body: { label: string; strategy: StrategySection }, signal?: AbortSignal) =>
    request<SavedStrategy>("/strategies", { method: "POST", body, signal }),

  presets: (signal?: AbortSignal) => request<PresetListResponse>("/presets", { signal }),

  createPreset: (body: { name: string; config: BacktestConfig }, signal?: AbortSignal) =>
    request<Preset>("/presets", { method: "POST", body, signal }),

  createBacktest: (body: BacktestConfig, signal?: AbortSignal) =>
    request<CreateBacktestResponse>("/backtests", { method: "POST", body, signal }),

  runs: (
    query: { status?: string; limit?: number; offset?: number } = {},
    signal?: AbortSignal,
  ) => request<RunListResponse>(`/backtests${buildQuery(query)}`, { signal }),

  run: (runId: string, signal?: AbortSignal) =>
    request<RunDetail>(`/backtests/${encodeURIComponent(runId)}`, { signal }),

  revealHoldout: (runId: string, signal?: AbortSignal) =>
    request<RunDetail>(`/backtests/${encodeURIComponent(runId)}/reveal-holdout`, {
      method: "POST",
      body: {},
      signal,
    }),

  compare: (ids: string[], signal?: AbortSignal) =>
    request<CompareResponse | RunSummary[]>(`/runs/compare${buildQuery({ ids: ids.join(",") })}`, {
      signal,
    }),

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

  experiments: (signal?: AbortSignal) =>
    request<ExperimentListResponse>("/experiments", { signal }),

  createExperiment: (body: unknown, signal?: AbortSignal) =>
    request<Experiment>("/experiments", { method: "POST", body, signal }),

  experiment: (id: string, signal?: AbortSignal) =>
    request<Experiment>(`/experiments/${encodeURIComponent(id)}`, { signal }),

  cancelExperiment: (id: string, signal?: AbortSignal) =>
    request<Experiment>(`/experiments/${encodeURIComponent(id)}/cancel`, {
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
