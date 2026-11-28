import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, indicatorList } from "../api/client";
import type { IndicatorSpec, Preset } from "../api/types";
import { useApiResource } from "../hooks/useApi";
import {
  DEFAULT_WORKSPACE_STATE,
  isBarAlias,
  isExecutionProfile,
  sanitiseIndicators,
  workspaceSearchString,
  type WorkspaceState,
} from "../lib/urlState";
import { profileLabel, defaultExecution } from "../lib/execution";
import {
  RAIL_DEFAULT_FEE_BPS,
  RAIL_DEFAULT_MAX_TRADES,
  RAIL_DEFAULT_SIZING,
} from "../lib/config";
import { fmtTimestamp } from "../lib/format";
import { isoDateOnly } from "../lib/date";
import { validateConfigJson } from "../lib/strategyValidation";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  SourceNote,
} from "../components/ui";

/**
 * A preset restores the workspace URL. The URL only carries what the workspace
 * can reproduce: instrument, bar type, date range, indicator instances,
 * strategy, and execution profile. A preset that changes sizing, max trades or
 * the advanced cost overrides carries its full config in the `strategy`
 * parameter instead, because the quick-mode URL cannot express them.
 */
const ADVANCED_EXECUTION_KEYS = [
  "fill_timing",
  "fee_bps",
  "slippage_bps",
  "min_abs_slippage",
  "book_staleness_s",
  "max_depth_levels",
  "fallback",
] as const;

/** True when a quick-mode URL would drop risk or execution settings. */
export function presetNeedsFullConfig(config: Preset["config"] | null | undefined): boolean {
  const risk = config?.risk;
  const sizing = risk?.sizing;
  if (sizing) {
    if (sizing.mode === "risk_pct") return true;
    if (typeof sizing.value === "number" && sizing.value !== RAIL_DEFAULT_SIZING.value) return true;
    if (typeof sizing.bankroll_usd === "number") return true;
  }
  if (typeof risk?.max_trades === "number" && risk.max_trades !== RAIL_DEFAULT_MAX_TRADES) return true;
  if (typeof risk?.max_position === "number") return true;

  const execution = config?.execution;
  if (!execution) return false;
  const profile = isExecutionProfile(execution.profile) ? execution.profile : "conservative";
  const baseline = defaultExecution(profile, RAIL_DEFAULT_FEE_BPS) as unknown as Record<
    string,
    unknown
  >;
  const actual = execution as unknown as Record<string, unknown>;
  for (const key of ADVANCED_EXECUTION_KEYS) {
    const value = actual[key];
    if (value === undefined) continue;
    if (key === "fee_bps") {
      if (value !== RAIL_DEFAULT_FEE_BPS) return true;
      continue;
    }
    if (value !== baseline[key]) return true;
  }
  return false;
}

export function presetWorkspaceSearch(preset: Preset): string {
  const config = preset.config;
  const strategy = config?.strategy;
  const barType =
    typeof strategy?.bar_type === "string" && isBarAlias(strategy.bar_type) ? strategy.bar_type : "1m";
  const profile =
    typeof config?.execution?.profile === "string" && isExecutionProfile(config.execution.profile)
      ? config.execution.profile
      : "conservative";
  const dateRange = config?.evaluation?.date_range as
    | { from?: string; to?: string; start?: string; end?: string }
    | undefined;
  // A preset may carry explicit rules alongside a builtin id for provenance.
  // Keep the rule tree instead of silently reducing the preset to the builtin.
  const hasRules = ["long_entry", "long_exit", "short_entry", "short_exit"].some(
    (key) => (strategy as unknown as Record<string, unknown> | undefined)?.[key] != null,
  );
  const state: WorkspaceState = {
    ...DEFAULT_WORKSPACE_STATE,
    instrument: strategy?.instrument_id ?? "",
    barType,
    from: isoDateOnly(dateRange?.from ?? dateRange?.start),
    to: isoDateOnly(dateRange?.to ?? dateRange?.end),
    indicators: sanitiseIndicators(strategy?.indicators ?? []),
    builtin: strategy?.builtin_id || DEFAULT_WORKSPACE_STATE.builtin,
    quickParams:
      !hasRules && strategy?.builtin_id ? (strategy.builtin_params ?? {}) : {},
    quickDirection:
      !hasRules && strategy?.direction === "long"
        ? "long"
        : !hasRules && strategy?.direction === "short"
          ? "short"
          : !hasRules && strategy?.direction === "both"
            ? "both"
            : "",
    strategyJson:
      config && (hasRules || !strategy?.builtin_id || presetNeedsFullConfig(config))
        ? JSON.stringify(config)
        : null,
    profile,
    runId: "",
    compare: [],
    phase: "all",
  };
  return workspaceSearchString(state);
}

export function PresetsPage() {
  const presetsResource = useApiResource((signal) => api.presets(signal), []);
  const specsResource = useApiResource(
    async (signal) => indicatorList(await api.indicators(signal)),
    [],
  );
  const specs: IndicatorSpec[] = specsResource.data ?? [];
  const [name, setName] = useState("");
  const [json, setJson] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<unknown | null>(null);
  const [created, setCreated] = useState(false);
  const [touched, setTouched] = useState(false);

  const validation = useMemo(() => {
    if (!touched || !json.trim()) return { ok: false, issues: [] };
    return validateConfigJson(json, specs);
  }, [json, specs, touched]);

  const presets = presetsResource.data?.presets ?? [];

  const create = async () => {
    setTouched(true);
    if (!name.trim()) {
      setCreateError(new Error("A preset name is required."));
      return;
    }
    if (!validation.ok) {
      setCreateError(new Error("Fix the configuration issues before saving."));
      return;
    }
    setCreating(true);
    setCreateError(null);
    setCreated(false);
    try {
      await api.createPreset({ name: name.trim(), config: JSON.parse(json) });
      setCreated(true);
      setName("");
      setJson("");
      setTouched(false);
      presetsResource.reload();
    } catch (caught) {
      setCreateError(caught);
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="mx-auto flex max-w-[1300px] flex-col gap-3 p-3">
      <header>
        <h1 className="text-[15px] font-semibold text-ink">Presets</h1>
        <p className="text-[11px] text-ink-muted">
          Saved experiment configurations: instrument, bar type, range, indicator instances,
          strategy, sizing, execution and evaluation. No folders or projects.
        </p>
      </header>

      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_460px]">
        <section className="panel overflow-hidden" aria-label="Saved presets">
          <div className="panel-title">
            <span>Saved configs</span>
            <button type="button" className="btn-ghost text-[10px]" onClick={presetsResource.reload}>
              Refresh
            </button>
          </div>
          {presetsResource.loading ? (
            <LoadingState rows={4} label="Loading presets" />
          ) : presetsResource.error ? (
            <ErrorState
              error={presetsResource.error}
              title="Presets unavailable"
              onRetry={presetsResource.reload}
            />
          ) : presets.length === 0 ? (
            <EmptyState
              title="No presets yet"
              message="Create a preset from a full backtest configuration. A preset is a saved config, not a promise that the strategy works."
            />
          ) : (
            <ul className="divide-y divide-line">
              {presets.map((preset) => {
                const config = preset.config;
                const search = presetWorkspaceSearch(preset);
                return (
                  <li key={preset.id} className="flex flex-wrap items-center gap-2 px-2 py-2">
                    <div className="min-w-0 flex-1">
                      <div className="text-[12px] font-medium text-ink">{preset.name}</div>
                      <div className="numeric truncate text-[10px] text-ink-faint">
                        {config?.strategy?.instrument_id || "instrument not available"} ·{" "}
                        {config?.strategy?.bar_type || "bar type not available"} ·{" "}
                        {isoDateOnly(config?.evaluation?.date_range?.from) || "from not available"} →{" "}
                        {isoDateOnly(config?.evaluation?.date_range?.to) || "to not available"} ·{" "}
                        {profileLabel(config?.execution?.profile)} · created{" "}
                        {fmtTimestamp(preset.created_at, false)}
                      </div>
                    </div>
                    <Link
                      className="btn"
                      to={{ pathname: "/", search }}
                      aria-label={`Open preset ${preset.name} in the workspace`}
                    >
                      Open
                    </Link>
                    <span className="numeric text-[9px] text-ink-faint" title={preset.id}>
                      {preset.id.slice(0, 10)}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
          <div className="border-t border-line px-2 py-1">
            <SourceNote>
              Opening a preset restores its instrument, range, indicator instances, strategy and
              execution profile into the shareable URL. Nothing is run automatically.
            </SourceNote>
          </div>
        </section>

        <section className="panel" aria-label="Create preset">
          <div className="panel-title">
            <span>New preset</span>
          </div>
          <div className="space-y-2 p-2">
            <label className="block">
              <span className="label">Name</span>
              <input
                className="input mt-1"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="e.g. EPL EMA cross — conservative"
                aria-label="Preset name"
              />
            </label>
            <label className="block">
              <span className="label">Configuration JSON</span>
              <textarea
                className="numeric mt-1 h-64 w-full resize-y rounded border border-line bg-surface-sunken p-2 text-[10px] leading-relaxed text-ink focus:border-accent focus:outline-none"
                value={json}
                onChange={(event) => {
                  setJson(event.target.value);
                  setTouched(true);
                  setCreated(false);
                }}
                placeholder={'{\n  "strategy": { "instrument_id": "…", "bar_type": "1m", "builtin_id": "…" },\n  "execution": { "profile": "conservative" },\n  "risk": { "sizing": { "mode": "fixed_stake", "value": 100 } },\n  "evaluation": { "date_range": { "from": "2024-10-01", "to": "2026-10-01" } }\n}'}
                spellCheck={false}
                aria-label="Preset configuration JSON"
                aria-invalid={touched && json.trim().length > 0 && !validation.ok}
              />
            </label>
            {touched && json.trim() && !validation.ok ? (
              <ul className="max-h-32 space-y-0.5 overflow-y-auto rounded border border-negative/40 p-1.5" role="alert">
                {validation.issues.slice(0, 8).map((issue, index) => (
                  <li key={`${issue.path}:${index}`} className="text-[10px] text-negative">
                    <span className="numeric">{issue.path}</span> — {issue.message}
                  </li>
                ))}
              </ul>
            ) : null}
            {touched && json.trim() && validation.ok ? (
              <p className="text-[10px] text-positive" role="status">
                Configuration is valid.
              </p>
            ) : null}
            <button
              type="button"
              className="btn-primary w-full"
              onClick={create}
              disabled={creating || !name.trim() || !json.trim()}
            >
              {creating ? "Saving…" : "Save preset"}
            </button>
            {createError ? (
              <p className="break-words text-[10px] text-negative" role="alert">
                {createError instanceof Error ? createError.message : "Preset was not saved."}
              </p>
            ) : null}
            {created ? (
              <p className="text-[10px] text-positive" role="status">
                Preset saved.
              </p>
            ) : null}
            <SourceNote>
              Validation uses the same declarative schema as a run: unknown indicators,
              out-of-range parameters and empty rule trees are rejected client-side, then again by
              the API. No Python is uploaded or evaluated.
            </SourceNote>
          </div>
        </section>
      </div>
    </div>
  );
}
