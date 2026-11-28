import { useEffect, useMemo, useRef, useState } from "react";
import { api, errorMessage } from "../api/client";
import type {
  BacktestConfig,
  BuiltinStrategy,
  ExecutionProfile,
  IndicatorInstance,
  IndicatorSpec,
  InstrumentQuality,
  PreflightResponse,
  StrategiesResponse,
} from "../api/types";
import {
  buildBacktestConfig,
  prettyConfig,
  RAIL_DEFAULT_FEE_BPS,
  RAIL_DEFAULT_MAX_TRADES,
  RAIL_DEFAULT_SIZING,
  type SizingState,
} from "../lib/config";
import {
  bookCoverageText,
  costEstimateText,
  defaultExecution,
  fallbackWarning,
  PROFILE_META,
  profileLabel,
} from "../lib/execution";
import { fmtDate, fmtDuration, fmtNumber, fmtPct, fmtUsd } from "../lib/format";
import { EXECUTION_PROFILES } from "../lib/barTypes";
import { validateConfigJson, type ValidationIssue } from "../lib/strategyValidation";
import { getStorage } from "../lib/storage";
import { ErrorState, FallbackBadge, GradeBadge } from "./ui";

interface StrategyRailProps {
  instrumentId: string;
  barType: string;
  from: string;
  to: string;
  indicators: IndicatorInstance[];
  specs: IndicatorSpec[];
  builtinId: string;
  onBuiltinId: (id: string) => void;
  initialQuickParams?: Record<string, number | boolean | string>;
  initialQuickDirection?: "long" | "short" | "both" | "";
  onQuickConfig?: (
    params: Record<string, number | boolean | string>,
    direction: "long" | "short" | "both",
  ) => void;
  strategyJson: string | null;
  onStrategyJson: (json: string | null) => void;
  profile: ExecutionProfile;
  onProfile: (profile: ExecutionProfile) => void;
  quality: InstrumentQuality | null;
  qualityLoading: boolean;
  onRun: (config: BacktestConfig) => void;
  submitting: boolean;
  submitError: unknown | null;
  runId: string | null;
  onSavePreset?: (name: string, config: BacktestConfig) => Promise<void> | void;
}

interface RailDefaults {
  feeBps: number;
  sizing: SizingState;
  maxTrades: number;
}

const RAIL_DEFAULTS_KEY = "htf.strategy.defaults";

function readRailDefaults(): RailDefaults {
  const fallback: RailDefaults = {
    feeBps: RAIL_DEFAULT_FEE_BPS,
    sizing: { ...RAIL_DEFAULT_SIZING },
    maxTrades: RAIL_DEFAULT_MAX_TRADES,
  };
  if (typeof window === "undefined") return fallback;
  try {
    const raw = getStorage().getItem(RAIL_DEFAULTS_KEY);
    if (!raw) return fallback;
    const parsed = JSON.parse(raw) as Partial<RailDefaults>;
    return {
      feeBps:
        typeof parsed.feeBps === "number" && parsed.feeBps >= 0 ? parsed.feeBps : fallback.feeBps,
      sizing:
        parsed.sizing && typeof parsed.sizing.value === "number"
          ? {
              mode: parsed.sizing.mode === "risk_pct" ? "risk_pct" : "fixed_stake",
              value: parsed.sizing.value,
              bankroll: parsed.sizing.bankroll ?? 1000,
            }
          : fallback.sizing,
      maxTrades:
        typeof parsed.maxTrades === "number" && parsed.maxTrades >= 1
          ? parsed.maxTrades
          : fallback.maxTrades,
    };
  } catch {
    return fallback;
  }
}

function defaultsFor(builtin: BuiltinStrategy | null): Record<string, number | boolean | string> {
  if (!builtin) return {};
  const params: Record<string, number | boolean | string> = {};
  for (const parameter of builtin.parameters ?? []) {
    params[parameter.name] = parameter.default;
  }
  return { ...params, ...(builtin.defaults ?? {}) };
}

function warningText(warning: unknown): string {
  if (typeof warning === "string") return warning;
  if (warning && typeof warning === "object") {
    const record = warning as { code?: unknown; message?: unknown };
    const message = typeof record.message === "string" ? record.message : "";
    const code = typeof record.code === "string" ? record.code : "";
    return message || code || "data warning";
  }
  return String(warning);
}

function qualityWarnings(quality: InstrumentQuality | null): string[] {
  return (quality?.warnings ?? []).map(warningText);
}

export function StrategyRail(props: StrategyRailProps) {
  const {
    instrumentId,
    barType,
    from,
    to,
    indicators,
    specs,
    builtinId,
    onBuiltinId,
    initialQuickParams,
    initialQuickDirection,
    onQuickConfig,
    strategyJson,
    onStrategyJson,
    profile,
    onProfile,
    quality,
    qualityLoading,
    onRun,
    submitting,
    submitError,
    runId,
    onSavePreset,
  } = props;

  const defaults = useMemo(readRailDefaults, []);
  const [feeBps, setFeeBps] = useState(defaults.feeBps);
  const [sizing, setSizing] = useState<SizingState>(defaults.sizing);
  const [maxTrades, setMaxTrades] = useState(defaults.maxTrades);
  const [direction, setDirection] = useState<"long" | "short" | "both">(
    initialQuickDirection || "long",
  );
  const [paramsByBuiltin, setParamsByBuiltin] = useState<
    Record<string, Record<string, number | boolean | string>>
  >({});
  const initialQuickParamsRef = useRef(initialQuickParams ?? {});
  const initialQuickDirectionRef = useRef(initialQuickDirection || "");
  const quickSeedRef = useRef(false);
  if (!quickSeedRef.current) {
    // The mount hydration effect can deliver the URL state after this rail has
    // already rendered once; keep the seed current until it is consumed.
    initialQuickParamsRef.current = initialQuickParams ?? {};
    initialQuickDirectionRef.current = initialQuickDirection || "";
  }
  const onQuickConfigRef = useRef(onQuickConfig);
  onQuickConfigRef.current = onQuickConfig;
  const [strategies, setStrategies] = useState<StrategiesResponse | null>(null);
  const [strategiesLoading, setStrategiesLoading] = useState(true);
  const [strategiesError, setStrategiesError] = useState<unknown | null>(null);
  const [preflight, setPreflight] = useState<PreflightResponse | null>(null);
  const [preflightLoading, setPreflightLoading] = useState(false);
  const [preflightError, setPreflightError] = useState<unknown | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [advancedIssues, setAdvancedIssues] = useState<ValidationIssue[]>([]);
  const [quickSeeded, setQuickSeeded] = useState(false);
  const [presetName, setPresetName] = useState("");
  const [presetState, setPresetState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const advanced = strategyJson !== null;

  useEffect(() => {
    const controller = new AbortController();
    setStrategiesLoading(true);
    api
      .strategies(controller.signal)
      .then((response) => {
        setStrategies(response);
        setStrategiesError(null);
      })
      .catch((caught: unknown) => {
        if (controller.signal.aborted) return;
        setStrategiesError(caught);
      })
      .finally(() => {
        if (!controller.signal.aborted) setStrategiesLoading(false);
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      getStorage().setItem(
        RAIL_DEFAULTS_KEY,
        JSON.stringify({ feeBps, sizing, maxTrades } satisfies RailDefaults),
      );
    } catch {
      // Storage is optional.
    }
  }, [feeBps, sizing, maxTrades]);

  const builtins = strategies?.builtins ?? [];
  const builtin = useMemo(
    () => builtins.find((item) => item.id === builtinId) ?? null,
    [builtins, builtinId],
  );

  useEffect(() => {
    if (!builtin) return;
    // Capture the seed before React runs the updater; setting the ref below
    // would otherwise hide it from the deferred updater.
    const canSeed = !quickSeedRef.current && builtin.id === builtinId;
    const seed = canSeed ? initialQuickParamsRef.current : null;
    setParamsByBuiltin((previous) => {
      if (previous[builtin.id]) return previous;
      const base = defaultsFor(builtin);
      return { ...previous, [builtin.id]: seed ? { ...base, ...seed } : base };
    });
    if (canSeed) {
      // The first catalogue entry to match the URL uses the URL's parameters
      // and direction; later user switches follow the built-in's own default.
      quickSeedRef.current = true;
      if (initialQuickDirectionRef.current) {
        setDirection(initialQuickDirectionRef.current as "long" | "short" | "both");
      } else if (builtin.direction) {
        setDirection(builtin.direction);
      }
    } else if (builtin.direction) {
      setDirection(builtin.direction);
    }
    setQuickSeeded(true);
  }, [builtin, builtinId, initialQuickDirection]);

  useEffect(() => {
    if (strategiesLoading || builtins.length === 0) return;
    if (!builtins.some((item) => item.id === builtinId)) {
      onBuiltinId(builtins[0].id);
    }
    // onBuiltinId is intentionally excluded: it is recreated by the parent and
    // the guard above makes repeated calls harmless.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [builtins, builtinId, strategiesLoading]);

  const builtinParams = useMemo(
    () => paramsByBuiltin[builtinId] ?? defaultsFor(builtin),
    [paramsByBuiltin, builtinId, builtin],
  );

  // Publish quick-authoring values so the shareable URL carries the strategy
  // parameters, not just the built-in id.
  useEffect(() => {
    if (advanced || !builtin || !quickSeeded) return;
    onQuickConfigRef.current?.(builtinParams, direction);
  }, [advanced, builtin, builtinParams, direction, quickSeeded]);

  const selectBuiltin = (id: string) => {
    const next = builtins.find((item) => item.id === id) ?? null;
    const nextDefaults = defaultsFor(next);
    const merged = { ...nextDefaults };
    for (const key of Object.keys(nextDefaults)) {
      if (key in builtinParams) merged[key] = builtinParams[key];
    }
    setParamsByBuiltin((previous) => ({ ...previous, [id]: merged }));
    onBuiltinId(id);
  };

  const setBuiltinParam = (name: string, value: number | boolean | string) => {
    setParamsByBuiltin((previous) => ({
      ...previous,
      [builtinId]: { ...builtinParams, [name]: value },
    }));
  };

  const advancedValue = useMemo(() => {
    if (!advanced || !strategyJson) return null;
    try {
      return JSON.parse(strategyJson) as unknown;
    } catch {
      return null;
    }
  }, [advanced, strategyJson]);

  // A preset or a shared URL loads a full config. Seed the rail controls from
  // it once so sizing, fee, max trades and direction survive the round trip
  // instead of silently reverting to remembered defaults.
  const [hydratedAdvanced, setHydratedAdvanced] = useState(false);
  useEffect(() => {
    if (hydratedAdvanced || !advancedValue) return;
    const asRecord = (value: unknown): Record<string, unknown> =>
      typeof value === "object" && value !== null && !Array.isArray(value)
        ? (value as Record<string, unknown>)
        : {};
    const root = asRecord(advancedValue);
    const strategy = asRecord(root.strategy);
    const execution = asRecord(root.execution);
    const risk = asRecord(root.risk);
    const sizingValue = asRecord(risk.sizing);

    if (strategy.direction === "long" || strategy.direction === "short" || strategy.direction === "both") {
      setDirection(strategy.direction);
    }
    if (typeof execution.fee_bps === "number" && Number.isFinite(execution.fee_bps)) {
      setFeeBps(execution.fee_bps);
    }
    const mode = sizingValue.mode === "risk_pct" ? "risk_pct" : "fixed_stake";
    if (typeof sizingValue.value === "number" && Number.isFinite(sizingValue.value)) {
      setSizing({
        mode,
        value: sizingValue.value,
        bankroll:
          typeof sizingValue.bankroll_usd === "number" && Number.isFinite(sizingValue.bankroll_usd)
            ? sizingValue.bankroll_usd
            : typeof sizingValue.bankroll === "number"
              ? sizingValue.bankroll
              : 1000,
      });
    }
    if (typeof risk.max_trades === "number" && risk.max_trades >= 1) {
      setMaxTrades(risk.max_trades);
    }
    setHydratedAdvanced(true);
  }, [advancedValue, hydratedAdvanced]);

  // Resolved before the config memo: the memo reads `warnings` as soon as the
  // user acknowledges them, so declaring the list further down would put it in
  // the temporal dead zone on the render that flips `acknowledged`.
  const warnings = useMemo(() => qualityWarnings(quality), [quality]);
  const warningKey = warnings.join("|");

  useEffect(() => {
    // An acknowledgement belongs to the instrument and the exact warning set
    // shown, so a newly appearing warning needs a fresh acknowledgement.
    setAcknowledged(false);
  }, [instrumentId, warningKey]);

  const config: BacktestConfig = useMemo(
    () =>
      buildBacktestConfig({
        instrumentId,
        barType,
        indicators,
        authoring: advanced ? "advanced" : "quick",
        builtinId,
        builtinParams,
        direction,
        advancedConfig: advancedValue,
        profile,
        feeBps,
        sizing,
        maxTrades,
        from,
        to,
        acknowledgedWarnings: acknowledged ? warnings : [],
      }),
    [
      instrumentId,
      barType,
      indicators,
      advanced,
      builtinId,
      builtinParams,
      direction,
      advancedValue,
      profile,
      feeBps,
      sizing,
      maxTrades,
      from,
      to,
      acknowledged,
      warnings,
    ],
  );

  useEffect(() => {
    if (!advanced || !strategyJson) {
      setAdvancedIssues([]);
      return;
    }
    const result = validateConfigJson(strategyJson, specs);
    setAdvancedIssues(result.issues);
  }, [advanced, strategyJson, specs]);

  useEffect(() => {
    if (!instrumentId) {
      setPreflight(null);
      setPreflightError(null);
      return;
    }
    const controller = new AbortController();
    setPreflightLoading(true);
    const handle = window.setTimeout(() => {
      api
        .preflight(
          {
            instrument_id: instrumentId,
            bar_type: barType,
            from: from || undefined,
            to: to || undefined,
            execution: defaultExecution(profile, feeBps),
            risk: config.risk,
            indicators: config.strategy.indicators,
          },
          controller.signal,
        )
        .then((response) => {
          setPreflight(response);
          setPreflightError(null);
        })
        .catch((caught: unknown) => {
          if (controller.signal.aborted) return;
          setPreflightError(caught);
        })
        .finally(() => {
          if (!controller.signal.aborted) setPreflightLoading(false);
        });
    }, 350);
    return () => {
      window.clearTimeout(handle);
      controller.abort();
    };
  }, [
    instrumentId,
    barType,
    from,
    to,
    profile,
    feeBps,
    sizing.mode,
    sizing.value,
    sizing.bankroll,
    maxTrades,
    indicators,
  ]);

  const startAdvanced = () => {
    if (advanced) return;
    onStrategyJson(prettyConfig(config));
  };

  const runDisabled =
    !instrumentId ||
    !from ||
    !to ||
    from > to ||
    submitting ||
    (advanced && (!advancedValue || advancedIssues.length > 0)) ||
    (!advanced && !builtin);

  const fallback = preflight
    ? fallbackWarning(preflight.requested, preflight.effective, preflight.fallback_reason)
    : null;

  const savePreset = async () => {
    if (!onSavePreset) return;
    if (!presetName.trim()) {
      setPresetState("error");
      return;
    }
    setPresetState("saving");
    try {
      await onSavePreset(presetName.trim(), config);
      setPresetState("saved");
      setPresetName("");
    } catch {
      setPresetState("error");
    }
  };

  return (
    <aside className="flex h-full min-h-0 flex-col overflow-y-auto" aria-label="Strategy rail">
      <div className="border-b border-line px-3 py-2">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-[12px] font-semibold uppercase tracking-[0.08em] text-ink">
            Strategy
          </h2>
          <div className="flex rounded border border-line p-0.5" role="group" aria-label="Authoring mode">
            <button
              type="button"
              className={`rounded px-2 py-0.5 text-[11px] ${
                !advanced ? "bg-surface-sunken font-semibold text-ink" : "text-ink-muted"
              }`}
              aria-pressed={!advanced}
              onClick={() => onStrategyJson(null)}
            >
              Quick
            </button>
            <button
              type="button"
              className={`rounded px-2 py-0.5 text-[11px] ${
                advanced ? "bg-surface-sunken font-semibold text-ink" : "text-ink-muted"
              }`}
              aria-pressed={advanced}
              onClick={startAdvanced}
            >
              Advanced
            </button>
          </div>
        </div>
      </div>

      <div className="space-y-3 p-3">
        {strategiesError ? (
          <ErrorState error={strategiesError} title="Strategy catalogue unavailable" />
        ) : null}

        {!advanced ? (
          <section className="space-y-2" aria-label="Built-in strategy">
            <label className="block">
              <span className="label">Built-in</span>
              <select
                className="input mt-1"
                value={builtinId}
                onChange={(event) => selectBuiltin(event.target.value)}
                disabled={strategiesLoading || builtins.length === 0}
              >
                {strategiesLoading ? <option value="">Loading catalogue…</option> : null}
                {!strategiesLoading && builtins.length === 0 ? (
                  <option value="">No built-ins registered</option>
                ) : null}
                {builtins.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.label}
                  </option>
                ))}
              </select>
            </label>
            {builtin?.description ? (
              <p className="text-[11px] leading-relaxed text-ink-muted">{builtin.description}</p>
            ) : null}
            {builtin?.provenance ? (
              <p className="numeric text-[10px] text-ink-faint">provenance: {builtin.provenance}</p>
            ) : null}

            {(builtin?.parameters ?? []).length > 0 ? (
              <div className="grid grid-cols-2 gap-2">
                {(builtin?.parameters ?? []).map((parameter) => (
                  <label key={parameter.name} className="block">
                    <span className="label">
                      {parameter.label ?? parameter.name}
                      {parameter.min != null || parameter.max != null ? (
                        <span className="ml-1 normal-case text-ink-faint">
                          [{parameter.min ?? "-∞"}, {parameter.max ?? "∞"}]
                        </span>
                      ) : null}
                    </span>
                    {parameter.type === "bool" || parameter.type === "boolean" ? (
                      <input
                        type="checkbox"
                        className="mt-1.5"
                        checked={builtinParams[parameter.name] === true}
                        onChange={(event) =>
                          setBuiltinParam(parameter.name, event.target.checked)
                        }
                        aria-label={parameter.label ?? parameter.name}
                      />
                    ) : (
                      <input
                        className="input-numeric mt-1"
                        type="number"
                        value={
                          typeof builtinParams[parameter.name] === "number"
                            ? (builtinParams[parameter.name] as number)
                            : ""
                        }
                        min={parameter.min ?? undefined}
                        max={parameter.max ?? undefined}
                        step={parameter.step ?? undefined}
                        onChange={(event) => {
                          const parsed = Number(event.target.value);
                          if (Number.isFinite(parsed)) setBuiltinParam(parameter.name, parsed);
                        }}
                        aria-label={parameter.label ?? parameter.name}
                      />
                    )}
                  </label>
                ))}
              </div>
            ) : (
              <p className="text-[10px] text-ink-faint">
                This built-in has no primary parameters exposed. Switch to Advanced to inspect its
                rule JSON.
              </p>
            )}

            <label className="block">
              <span className="label">Direction</span>
              <select
                className="input mt-1"
                value={direction}
                onChange={(event) =>
                  setDirection(event.target.value as "long" | "short" | "both")
                }
              >
                <option value="long">Long</option>
                <option value="short">Short (buy NO token)</option>
                <option value="both">Both</option>
              </select>
            </label>
          </section>
        ) : (
          <section className="space-y-2" aria-label="Advanced rule JSON">
            <div className="flex items-center justify-between">
              <span className="label">Full configuration JSON</span>
              <button
                type="button"
                className="btn-ghost text-[10px]"
                onClick={() => onStrategyJson(prettyConfig(config))}
                title="Replace the editor with the current form values"
              >
                Load current
              </button>
            </div>
            <textarea
              className="numeric h-56 w-full resize-y rounded border border-line bg-surface-sunken p-2 text-[10px] leading-relaxed text-ink focus:border-accent focus:outline-none"
              value={strategyJson ?? ""}
              onChange={(event) => onStrategyJson(event.target.value)}
              spellCheck={false}
              aria-label="Strategy configuration JSON"
              aria-invalid={advancedIssues.length > 0}
            />
            {advancedIssues.length > 0 ? (
              <ul className="max-h-32 space-y-1 overflow-y-auto rounded border border-negative/40 p-1.5" role="alert">
                {advancedIssues.slice(0, 10).map((issue, index) => (
                  <li key={`${issue.path}:${index}`} className="text-[10px] text-negative">
                    <span className="numeric">{issue.path}</span> — {issue.message}
                  </li>
                ))}
                {advancedIssues.length > 10 ? (
                  <li className="text-[10px] text-ink-faint">
                    +{advancedIssues.length - 10} more issue(s)
                  </li>
                ) : null}
              </ul>
            ) : (
              <p className="text-[10px] text-positive" role="status">
                Rule JSON is valid. Unknown indicators, out-of-range parameters and empty trees are
                rejected before a job exists.
              </p>
            )}
            <p className="text-[10px] leading-relaxed text-ink-faint">
              The instrument, bar type, indicator instances, execution profile, sizing and date
              range from this rail are applied over the JSON. Rules stay declarative; no code is
              uploaded or evaluated.
            </p>
          </section>
        )}

        <section className="space-y-2 border-t border-line pt-2" aria-label="Sizing">
          <span className="label">Sizing</span>
          <div className="flex gap-1.5">
            <select
              className="input w-auto py-1"
              value={sizing.mode}
              onChange={(event) =>
                setSizing((current) => ({
                  ...current,
                  mode: event.target.value as SizingState["mode"],
                }))
              }
              aria-label="Sizing mode"
            >
              <option value="fixed_stake">Fixed stake (USD)</option>
              <option value="risk_pct">Risk % of bankroll</option>
            </select>
            <input
              className="input-numeric py-1"
              type="number"
              min={0.01}
              step={sizing.mode === "risk_pct" ? 0.1 : 1}
              value={sizing.value}
              onChange={(event) =>
                setSizing((current) => ({ ...current, value: Number(event.target.value) }))
              }
              aria-label={sizing.mode === "fixed_stake" ? "Stake in USD" : "Risk percent"}
            />
          </div>
          {sizing.mode === "risk_pct" ? (
            <label className="block">
              <span className="label">Bankroll USD</span>
              <input
                className="input-numeric mt-1"
                type="number"
                min={1}
                step={1}
                value={sizing.bankroll}
                onChange={(event) =>
                  setSizing((current) => ({ ...current, bankroll: Number(event.target.value) }))
                }
                aria-label="Bankroll in USD"
              />
            </label>
          ) : null}
          <label className="block">
            <span className="label">Maximum trades</span>
            <input
              className="input-numeric mt-1"
              type="number"
              min={1}
              step={1}
              value={maxTrades}
              onChange={(event) => setMaxTrades(Number(event.target.value))}
              aria-label="Maximum trades"
            />
          </label>
          <p className="text-[10px] leading-relaxed text-ink-faint">
            {sizing.mode === "fixed_stake"
              ? `Stake ${fmtUsd(sizing.value)} per entry.`
              : `Risk ${fmtNumber(sizing.value, 2)}% of ${fmtUsd(sizing.bankroll)} bankroll per entry.`}{" "}
            One position per instrument; no pyramiding. Fractional Kelly is not offered in v1.
          </p>
          {preflight?.sizing ? (
            <dl
              className="grid grid-cols-2 gap-x-3 gap-y-1 rounded border border-line px-1.5 py-1 text-[10px]"
              aria-label="Preflight sizing preview"
            >
              <div>
                <dt className="text-ink-faint">Stake</dt>
                <dd className="numeric text-ink-muted">{fmtUsd(preflight.sizing.stake_usd)}</dd>
              </div>
              <div>
                <dt className="text-ink-faint">Contracts</dt>
                <dd className="numeric text-ink-muted">
                  {fmtNumber(preflight.sizing.contracts, 2)}
                </dd>
              </div>
              <div>
                <dt className="text-ink-faint">Max loss</dt>
                <dd className="numeric text-ink-muted">{fmtUsd(preflight.sizing.max_loss_usd)}</dd>
              </div>
              <div>
                <dt className="text-ink-faint">Max payout</dt>
                <dd className="numeric text-ink-muted">
                  {fmtUsd(preflight.sizing.max_payout_usd)}
                </dd>
              </div>
              <div>
                <dt className="text-ink-faint">Max profit</dt>
                <dd className="numeric text-ink-muted">
                  {fmtUsd(preflight.sizing.max_profit_usd)}
                </dd>
              </div>
            </dl>
          ) : null}
        </section>

        <section className="space-y-1.5 border-t border-line pt-2" aria-label="Execution profile">
          <div className="flex items-center justify-between">
            <span className="label">Execution profile</span>
            <span className="text-[10px] text-ink-faint">Fee modelled below</span>
          </div>
          <fieldset className="space-y-1">
            <legend className="sr-only">Execution profile</legend>
            {EXECUTION_PROFILES.map((id) => {
              const meta = PROFILE_META[id];
              return (
                <label
                  key={id}
                  className={`flex cursor-pointer items-start gap-2 rounded border px-2 py-1.5 ${
                    profile === id
                      ? "border-accent bg-surface-sunken"
                      : "border-line hover:border-line-strong"
                  }`}
                >
                  <input
                    type="radio"
                    name="execution-profile"
                    value={id}
                    checked={profile === id}
                    onChange={() => onProfile(id)}
                    className="mt-0.5"
                  />
                  <span className="min-w-0">
                    <span className="flex items-center gap-1.5">
                      <span className="text-[12px] font-medium text-ink">{meta.label}</span>
                      <span className="numeric text-[9px] text-ink-faint">
                        fee {meta.fee} · slip {meta.slippage} · min {meta.minAbs}
                      </span>
                      {meta.fallback ? <span className="badge-warn">falls back</span> : null}
                    </span>
                    <span className="block text-[10px] leading-snug text-ink-muted">
                      {meta.description}
                    </span>
                  </span>
                </label>
              );
            })}
          </fieldset>
          <label className="block pt-1">
            <span className="label">Fee override (bps, 0 = no fee modelled)</span>
            <input
              className="input-numeric mt-1"
              type="number"
              min={0}
              step={0.1}
              value={feeBps}
              onChange={(event) => setFeeBps(Number(event.target.value))}
              aria-label="Fee in basis points"
            />
          </label>
        </section>

        <section className="space-y-1.5 border-t border-line pt-2" aria-label="Execution preflight">
          <div className="flex items-center gap-2">
            <span className="label">Preflight</span>
            {preflightLoading ? <span className="badge">checking…</span> : null}
            {preflight ? (
              <span className="badge">
                requested {profileLabel(preflight.requested)} · effective{" "}
                {profileLabel(preflight.effective)}
              </span>
            ) : null}
          </div>
          {!instrumentId ? (
            <p className="text-[10px] text-ink-faint">Select an instrument to run a preflight.</p>
          ) : preflightError ? (
            <p className="text-[10px] text-warn" role="status">
              Preflight unavailable: {errorMessage(preflightError)}. The run will still ask the
              server to resolve the profile.
            </p>
          ) : preflight ? (
            <>
              <FallbackBadge
                requested={preflight.requested}
                effective={preflight.effective}
                reason={preflight.fallback_reason}
                full
              />
              <p className="numeric text-[10px] text-ink-muted">
                cost estimate: {costEstimateText(preflight)}
              </p>
              <p className="text-[10px] text-ink-faint">
                Book coverage: {bookCoverageText(preflight.book_coverage)}
              </p>
              {(preflight.warnings ?? []).map((warning) => (
                <p key={warningText(warning)} className="text-[10px] text-warn">
                  {warningText(warning)}
                </p>
              ))}
              {fallback ? (
                <p className="text-[10px] leading-relaxed text-warn" role="note">
                  The run keeps its requested profile in the manifest but is priced at{" "}
                  {profileLabel(preflight.effective)}. Never Frictionless.
                </p>
              ) : null}
            </>
          ) : (
            <p className="text-[10px] text-ink-faint">Awaiting preflight response…</p>
          )}
        </section>

        <section className="space-y-1.5 border-t border-line pt-2" aria-label="Data quality preflight">
          <div className="flex items-center justify-between">
            <span className="label">Data quality</span>
            {qualityLoading ? (
              <span className="badge">loading…</span>
            ) : (
              <GradeBadge grade={quality?.quality_grade ?? null} />
            )}
          </div>
          <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-[10px]">
            <div>
              <dt className="text-ink-faint">Available period</dt>
              <dd className="numeric text-ink-muted">
                {quality?.available_from && quality?.available_to
                  ? `${fmtDate(quality.available_from)} → ${fmtDate(quality.available_to)}`
                  : "not available"}
              </dd>
            </div>
            <div>
              <dt className="text-ink-faint">Fills</dt>
              <dd className="numeric text-ink-muted">
                {quality?.trade_count != null
                  ? quality.trade_count.toLocaleString("en-GB")
                  : "not available"}
              </dd>
            </div>
            <div>
              <dt className="text-ink-faint">Active minutes</dt>
              <dd className="numeric text-ink-muted">
                {quality?.active_minute_pct != null
                  ? fmtPct(quality.active_minute_pct, 1)
                  : "not available"}
              </dd>
            </div>
            <div>
              <dt className="text-ink-faint">Longest gap</dt>
              <dd className="numeric text-ink-muted">{fmtDuration(quality?.longest_gap_s)}</dd>
            </div>
            <div>
              <dt className="text-ink-faint">Median trade</dt>
              <dd className="numeric text-ink-muted">{fmtUsd(quality?.median_trade_usd)}</dd>
            </div>
            <div>
              <dt className="text-ink-faint">Settlement</dt>
              <dd className="numeric text-ink-muted">
                {quality
                  ? quality.settlement_known && quality.settlement_value != null
                    ? `settled ${quality.settlement_value}`
                    : "not available"
                  : "not available"}
              </dd>
            </div>
          </dl>
          {warnings.length > 0 ? (
            <>
              <ul className="space-y-0.5">
                {warnings.map((warning) => (
                  <li key={warning} className="text-[10px] leading-snug text-warn">
                    ⚠ {warning}
                  </li>
                ))}
              </ul>
              <label className="flex items-start gap-1.5 text-[10px] text-ink-muted">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={acknowledged}
                onChange={(event) => setAcknowledged(event.target.checked)}
              />
                I acknowledge these data warnings; they will be recorded in the manifest.
              </label>
            </>
          ) : (
            <p className="text-[10px] text-ink-faint">
              Warnings are advisory and do not block a run; acknowledged warnings are recorded in
              the manifest by the backend.
            </p>
          )}
        </section>

        <div className="space-y-1.5 border-t border-line pt-2">
          <button
            type="button"
            className="btn-primary w-full py-2"
            onClick={() => onRun(config)}
            disabled={runDisabled}
          >
            {submitting ? "Submitting…" : "Run backtest"}
          </button>
          {runDisabled && !submitting ? (
            <p className="text-[10px] text-ink-faint">
              {!instrumentId
                ? "Select an instrument first."
                : !from || !to
                  ? "Set a from and to date before running."
                  : from > to
                    ? "The from date must not be after the to date."
                    : advanced
                      ? "Fix the rule JSON issues before running."
                      : "Select a built-in strategy."}
            </p>
          ) : null}
          {submitError ? (
            <p className="break-words text-[10px] text-negative" role="alert">
              Run rejected: {errorMessage(submitError)}
            </p>
          ) : null}
          {runId ? (
            <p className="numeric text-[10px] text-ink-faint" title={runId}>
              run {runId}
            </p>
          ) : null}

          {onSavePreset ? (
            <div className="flex items-center gap-1.5 pt-1">
              <input
                className="input py-1 text-[11px]"
                placeholder="Preset name"
                value={presetName}
                onChange={(event) => {
                  setPresetName(event.target.value);
                  setPresetState("idle");
                }}
                aria-label="Preset name"
              />
              <button
                type="button"
                className="btn shrink-0"
                onClick={savePreset}
                disabled={presetState === "saving" || !presetName.trim()}
              >
                {presetState === "saving" ? "Saving…" : "Save preset"}
              </button>
            </div>
          ) : null}
          {presetState === "saved" ? (
            <p className="text-[10px] text-positive" role="status">
              Preset saved.
            </p>
          ) : null}
          {presetState === "error" ? (
            <p className="text-[10px] text-negative" role="alert">
              Preset was not saved. A name is required and the API must accept the config.
            </p>
          ) : null}
        </div>
      </div>
    </aside>
  );
}
