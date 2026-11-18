import type { BarAlias, ExecutionProfile, IndicatorInstance, RuleNode } from "../api/types";
import { BAR_ALIASES, EXECUTION_PROFILES } from "./barTypes";
import { getStorage } from "./storage";

export type PhaseFilter = "all" | "pre_match" | "in_play" | "post_match";

export interface WorkspaceState {
  instrument: string;
  barType: BarAlias;
  from: string;
  to: string;
  indicators: IndicatorInstance[];
  builtin: string;
  strategyJson: string | null;
  profile: ExecutionProfile;
  runId: string;
  compare: string[];
  phase: PhaseFilter;
}

export interface ParsedWorkspace {
  state: WorkspaceState;
  issues: string[];
}

export const DEFAULT_WORKSPACE_STATE: WorkspaceState = {
  instrument: "",
  barType: "1m",
  from: "",
  to: "",
  indicators: [],
  builtin: "nautilus_ema_cross",
  strategyJson: null,
  profile: "conservative",
  runId: "",
  compare: [],
  phase: "all",
};

const STORAGE_KEY = "htf.workspace.defaults";
const MAX_COMPARE = 3;
const MAX_INDICATORS = 24;

const PHASES: PhaseFilter[] = ["all", "pre_match", "in_play", "post_match"];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function isBarAlias(value: unknown): value is BarAlias {
  return typeof value === "string" && BAR_ALIASES.some((item) => item.alias === value);
}

export function isExecutionProfile(value: unknown): value is ExecutionProfile {
  return typeof value === "string" && (EXECUTION_PROFILES as string[]).includes(value);
}

export function isPhaseFilter(value: unknown): value is PhaseFilter {
  return typeof value === "string" && (PHASES as string[]).includes(value);
}

/** Strict YYYY-MM-DD, rejecting e.g. 2024-02-31. */
export function isDateString(value: unknown): value is string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}

function sanitiseIndicator(value: unknown): IndicatorInstance | null {
  if (!isRecord(value)) return null;
  const id = value.instance_id ?? value.id;
  const type = value.type;
  if (typeof id !== "string" || !id.trim() || typeof type !== "string" || !type.trim()) {
    return null;
  }
  const rawParams = isRecord(value.params) ? value.params : {};
  const params: Record<string, number | boolean | string> = {};
  for (const [key, param] of Object.entries(rawParams)) {
    if (typeof param === "number" && Number.isFinite(param)) params[key] = param;
    else if (typeof param === "boolean" || typeof param === "string") params[key] = param;
  }
  const indicator: IndicatorInstance = {
    instance_id: id,
    type,
    params,
  };
  if (typeof value.color === "string" && /^#[0-9a-fA-F]{3,8}$/.test(value.color)) {
    indicator.color = value.color;
  }
  if (typeof value.visible === "boolean") indicator.visible = value.visible;
  return indicator;
}

export function sanitiseIndicators(value: unknown): IndicatorInstance[] {
  if (!Array.isArray(value)) return [];
  const seen = new Set<string>();
  const result: IndicatorInstance[] = [];
  for (const item of value) {
    const indicator = sanitiseIndicator(item);
    if (!indicator || seen.has(indicator.instance_id)) continue;
    seen.add(indicator.instance_id);
    result.push(indicator);
    if (result.length >= MAX_INDICATORS) break;
  }
  return result;
}

function parseStrategyJson(raw: string | null): { json: string | null; issue?: string } {
  if (!raw) return { json: null };
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!isRecord(parsed)) return { json: null, issue: "strategy JSON is not an object" };
    if (!isRecord(parsed.strategy) && !isRecord(parsed.long_entry) && !isRecord(parsed.all)) {
      return { json: null, issue: "strategy JSON has no strategy section" };
    }
    return { json: raw };
  } catch {
    return { json: null, issue: "strategy JSON could not be parsed" };
  }
}

/**
 * Parse a query string into workspace state. Invalid values never throw: they
 * fall back to the default and add a human-readable issue.
 */
export function parseWorkspaceSearch(search: string): ParsedWorkspace {
  const params = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
  const issues: string[] = [];
  const state: WorkspaceState = { ...DEFAULT_WORKSPACE_STATE, indicators: [], compare: [] };

  const instrument = params.get("instrument")?.trim() ?? "";
  if (instrument) state.instrument = instrument.slice(0, 200);

  const bar = params.get("bar");
  if (bar) {
    if (isBarAlias(bar)) state.barType = bar;
    else issues.push(`unknown bar type "${bar}", using 1m`);
  }

  for (const key of ["from", "to"] as const) {
    const value = params.get(key);
    if (!value) continue;
    if (isDateString(value)) state[key] = value;
    else issues.push(`invalid ${key} date "${value}"`);
  }

  if (state.from && state.to && state.from > state.to) {
    issues.push("from is after to");
    state.from = "";
    state.to = "";
  }

  const rawIndicators = params.get("ind");
  if (rawIndicators) {
    try {
      const parsed: unknown = JSON.parse(rawIndicators);
      const indicators = sanitiseIndicators(parsed);
      if (!Array.isArray(parsed)) issues.push("indicator list is not an array");
      else if (indicators.length === 0 && parsed.length > 0) {
        issues.push("indicator list had no valid entries");
      }
      state.indicators = indicators;
    } catch {
      issues.push("indicator list JSON could not be parsed");
    }
  }

  const builtin = params.get("builtin");
  if (builtin) state.builtin = builtin.slice(0, 120);

  const strategy = parseStrategyJson(params.get("strategy"));
  if (strategy.issue) issues.push(strategy.issue);
  state.strategyJson = strategy.json;

  const profile = params.get("profile");
  if (profile) {
    if (isExecutionProfile(profile)) state.profile = profile;
    else issues.push(`unknown execution profile "${profile}", using conservative`);
  }

  const runId = params.get("run");
  if (runId) state.runId = runId.slice(0, 200);

  const compare = params.get("compare");
  if (compare) {
    state.compare = compare
      .split(",")
      .map((id) => id.trim())
      .filter(Boolean)
      .slice(0, MAX_COMPARE);
  }

  const phase = params.get("phase");
  if (phase) {
    if (isPhaseFilter(phase)) state.phase = phase;
    else issues.push(`unknown phase "${phase}"`);
  }

  return { state, issues };
}

export function serializeWorkspaceState(state: WorkspaceState): URLSearchParams {
  const params = new URLSearchParams();
  if (state.instrument) params.set("instrument", state.instrument);
  if (state.barType !== DEFAULT_WORKSPACE_STATE.barType) params.set("bar", state.barType);
  if (state.from) params.set("from", state.from);
  if (state.to) params.set("to", state.to);
  if (state.indicators.length > 0) {
    params.set(
      "ind",
      JSON.stringify(
        state.indicators.map((indicator) => ({
          instance_id: indicator.instance_id,
          type: indicator.type,
          params: indicator.params,
          ...(indicator.color ? { color: indicator.color } : {}),
          ...(typeof indicator.visible === "boolean" ? { visible: indicator.visible } : {}),
        })),
      ),
    );
  }
  if (state.strategyJson) params.set("strategy", state.strategyJson);
  else if (state.builtin && state.builtin !== DEFAULT_WORKSPACE_STATE.builtin) {
    params.set("builtin", state.builtin);
  }
  if (state.profile !== DEFAULT_WORKSPACE_STATE.profile) params.set("profile", state.profile);
  if (state.runId) params.set("run", state.runId);
  if (state.compare.length > 0) params.set("compare", state.compare.join(","));
  if (state.phase !== "all") params.set("phase", state.phase);
  return params;
}

export function workspaceSearchString(state: WorkspaceState): string {
  const query = serializeWorkspaceState(state).toString();
  return query ? `?${query}` : "";
}

export function loadRememberedDefaults(): WorkspaceState {
  const storage = safeStorage();
  if (!storage) return { ...DEFAULT_WORKSPACE_STATE };
  try {
    const raw = storage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULT_WORKSPACE_STATE };
    const parsed: unknown = JSON.parse(raw);
    if (!isRecord(parsed)) return { ...DEFAULT_WORKSPACE_STATE };
    return {
      ...DEFAULT_WORKSPACE_STATE,
      ...parseWorkspaceSearch(
        serializeWorkspaceState({
          ...DEFAULT_WORKSPACE_STATE,
          ...(parsed as Partial<WorkspaceState>),
          indicators: sanitiseIndicators((parsed as Partial<WorkspaceState>).indicators),
          compare: [],
          runId: "",
        }).toString(),
      ).state,
      runId: "",
      compare: [],
    };
  } catch {
    return { ...DEFAULT_WORKSPACE_STATE };
  }
}

export function rememberWorkspaceSettings(state: WorkspaceState): void {
  const storage = safeStorage();
  if (!storage) return;
  try {
    const remembered: WorkspaceState = {
      ...state,
      runId: "",
      compare: [],
    };
    storage.setItem(STORAGE_KEY, JSON.stringify(remembered));
  } catch {
    // Storage can be unavailable (private mode, quota); the app still works.
  }
}

export function forgetWorkspaceSettings(): void {
  const storage = safeStorage();
  if (!storage) return;
  try {
    storage.removeItem(STORAGE_KEY);
  } catch {
    // Ignore storage failures.
  }
}

/** window.localStorage can be absent in SSR and restricted in private mode. */
export function safeStorage(): Storage {
  return getStorage();
}

/** A rule tree used by advanced mode; exported for the validation tests. */
export type { RuleNode };
