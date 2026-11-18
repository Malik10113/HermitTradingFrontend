import { Fragment, useCallback, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import { instrumentLabel, normaliseRun } from "../../api/normalise";
import type {
  BuiltinStrategy,
  Experiment,
  IndicatorParameter,
  Instrument,
  RunDetail,
  RunStatus,
} from "../../api/types";
import { useApiResource } from "../../hooks/useApi";
import { isoDateOnly } from "../../lib/date";
import { fmtInteger, fmtNumber, fmtPct, fmtTimestamp, fmtUsd } from "../../lib/format";
import { ErrorState, LoadingState, SourceNote, StatusPill } from "../ui";

/** The backend's default grid cap; the hard cap is 200 but the UI stays at 50. */
export const MAX_SWEEP_VARIANTS = 50;

export interface SweepAxis {
  path: string;
  values: number[];
}

export interface SweepRequestInput {
  name: string;
  builtinId: string;
  builtinDefaults: Record<string, number | boolean | string>;
  instrumentId: string;
  from: string;
  to: string;
  axes: SweepAxis[];
}

/** Parse a comma-separated axis, de-duplicating values and reporting junk. */
export function parseAxisValues(text: string): { values: number[]; invalid: string[] } {
  const values: number[] = [];
  const invalid: string[] = [];
  for (const token of text.split(",")) {
    const trimmed = token.trim();
    if (!trimmed) continue;
    const value = Number(trimmed);
    if (!Number.isFinite(value)) {
      invalid.push(trimmed);
      continue;
    }
    if (!values.includes(value)) values.push(value);
  }
  return { values, invalid };
}

/** Cartesian product size for one or two axes; zero when an axis is empty. */
export function variantCount(axes: SweepAxis[]): number {
  if (axes.length === 0) return 0;
  return axes.reduce((total, axis) => total * (axis.values.length || 0), 1);
}

/**
 * Build the POST /api/experiments body. It mirrors the backend contract exactly:
 * a base RunConfig plus 1–2 axes whose values are written into the base config.
 */
export function buildExperimentRequest(input: SweepRequestInput) {
  const axes = input.axes
    .filter((axis) => axis.path && axis.values.length > 0)
    .map((axis) => ({ path: axis.path, values: [...axis.values] }));
  return {
    name: input.name.trim() || "Parameter sweep",
    base: {
      strategy: {
        instrument_id: input.instrumentId,
        bar_type: "1m",
        builtin_id: input.builtinId,
        builtin_params: { ...input.builtinDefaults },
      },
      execution: { profile: "conservative" },
      risk: { sizing: { mode: "fixed_stake", value: 100, bankroll_usd: 1000 } },
      evaluation: { date_range: { from: input.from, to: input.to } },
    },
    axes,
  };
}

/** Default sweep values come from the parameter's own default/bounds. */
export function defaultSweepValues(parameter: IndicatorParameter | null): number[] {
  if (!parameter || typeof parameter.default !== "number" || !Number.isFinite(parameter.default)) {
    return [];
  }
  const base = parameter.default;
  const step =
    typeof parameter.step === "number" && Number.isFinite(parameter.step) && parameter.step > 0
      ? parameter.step
      : 1;
  const candidates = [base, base + step, base + 2 * step];
  const min = typeof parameter.min === "number" ? parameter.min : -Infinity;
  const max = typeof parameter.max === "number" ? parameter.max : Infinity;
  return candidates.filter(
    (value, index) => value >= min && value <= max && candidates.indexOf(value) === index,
  );
}

export interface ExperimentAxis {
  path: string;
  values: Array<number | string>;
}

export interface ExperimentDetail {
  id: string;
  name: string | null;
  status: RunStatus;
  completed: number | null;
  total: number | null;
  created_at: string | null;
  axes: ExperimentAxis[];
  runs: RunDetail[];
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

/** Turn GET /api/experiments/{id} into the shape the panel renders. */
export function parseExperimentDetail(raw: unknown): ExperimentDetail {
  const source = asRecord(raw);
  const config = asRecord(source.config);
  const axes: ExperimentAxis[] = asArray(config.axes)
    .map((item) => {
      const axis = asRecord(item);
      const path = typeof axis.path === "string" ? axis.path : "";
      const values = asArray(axis.values).filter(
        (value): value is number | string =>
          typeof value === "number" || typeof value === "string",
      );
      return { path, values };
    })
    .filter((axis) => axis.path !== "" && axis.values.length > 0);
  return {
    id: typeof source.id === "string" ? source.id : "",
    name: typeof source.name === "string" && source.name ? source.name : null,
    status: (typeof source.status === "string" ? source.status : "queued") as RunStatus,
    completed: typeof source.completed === "number" ? source.completed : null,
    total: typeof source.total === "number" ? source.total : null,
    created_at: typeof source.created_at === "string" ? source.created_at : null,
    axes,
    runs: asArray(source.runs).map((run) => normaliseRun(run)),
  };
}

export function axisLabel(path: string): string {
  const leaf = path.split(".").pop() ?? path;
  return leaf.replaceAll("_", " ");
}

function readPath(source: unknown, path: string): unknown {
  let current: unknown = source;
  for (const part of path.split(".")) {
    if (current === null || current === undefined) return undefined;
    if (Array.isArray(current)) {
      const index = Number(part);
      current = Number.isInteger(index) ? current[index] : undefined;
    } else if (typeof current === "object") {
      current = (current as Record<string, unknown>)[part];
    } else {
      return undefined;
    }
  }
  return current;
}

export type VariantMetric =
  | "net_pnl_usd"
  | "gross_pnl_usd"
  | "net_return_pct"
  | "sharpe"
  | "max_drawdown_pct"
  | "trade_count";

export const VARIANT_METRICS: Array<{ value: VariantMetric; label: string }> = [
  { value: "net_pnl_usd", label: "Net P&L" },
  { value: "gross_pnl_usd", label: "Gross P&L" },
  { value: "net_return_pct", label: "Net return" },
  { value: "sharpe", label: "Sharpe" },
  { value: "max_drawdown_pct", label: "Max drawdown" },
  { value: "trade_count", label: "Trades" },
];

export interface VariantRow {
  run_id: string;
  status: RunStatus;
  metrics_scope: string | null;
  values: Record<string, unknown>;
  gross_pnl_usd: number | null;
  net_pnl_usd: number | null;
  net_return_pct: number | null;
  sharpe: number | null;
  max_drawdown_pct: number | null;
  trade_count: number | null;
}

function metricNumber(metrics: RunDetail["metrics"], key: keyof NonNullable<RunDetail["metrics"]>) {
  const value = metrics?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function variantMetricValue(row: VariantRow, metric: VariantMetric): number | null {
  return row[metric] ?? null;
}

export function buildVariantRows(axes: ExperimentAxis[], runs: RunDetail[]): VariantRow[] {
  return runs.map((run) => {
    const values: Record<string, unknown> = {};
    for (const axis of axes) {
      values[axis.path] = readPath(run.config, axis.path);
    }
    return {
      run_id: run.run_id,
      status: run.status,
      metrics_scope:
        typeof (run as unknown as Record<string, unknown>).metrics_scope === "string"
          ? ((run as unknown as Record<string, unknown>).metrics_scope as string)
          : null,
      values,
      gross_pnl_usd: metricNumber(run.metrics, "gross_pnl_usd"),
      net_pnl_usd: metricNumber(run.metrics, "net_pnl_usd"),
      net_return_pct:
        metricNumber(run.metrics, "net_return_pct") ?? metricNumber(run.metrics, "return_pct"),
      sharpe: metricNumber(run.metrics, "sharpe"),
      max_drawdown_pct: metricNumber(run.metrics, "max_drawdown_pct"),
      trade_count: metricNumber(run.metrics, "trade_count"),
    };
  });
}

export type VariantSortKey =
  | "variant"
  | "status"
  | "gross_pnl_usd"
  | "net_pnl_usd"
  | "net_return_pct"
  | "trade_count";

export type SortDirection = "asc" | "desc";

function compareNullable(a: number | null, b: number | null): number {
  if (a === b) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  return a < b ? -1 : 1;
}

/** Sort variant rows; null/absent metrics always sort last, never as zero. */
export function sortVariantRows(
  rows: VariantRow[],
  key: VariantSortKey,
  direction: SortDirection,
  axes: ExperimentAxis[] = [],
): VariantRow[] {
  const sign = direction === "asc" ? 1 : -1;
  const compareVariant = (a: VariantRow, b: VariantRow) => {
    for (const axis of axes) {
      const left = a.values[axis.path];
      const right = b.values[axis.path];
      const leftNumber = typeof left === "number" ? left : Number(left);
      const rightNumber = typeof right === "number" ? right : Number(right);
      const bothNumeric = Number.isFinite(leftNumber) && Number.isFinite(rightNumber);
      const compared = bothNumeric
        ? leftNumber === rightNumber
          ? 0
          : leftNumber < rightNumber
            ? -1
            : 1
        : String(left ?? "").localeCompare(String(right ?? ""));
      if (compared !== 0) return compared;
    }
    return 0;
  };
  return [...rows].sort((a, b) => {
    if (key === "variant") return sign * compareVariant(a, b);
    if (key === "status") return sign * a.status.localeCompare(b.status);
    const compared = compareNullable(a[key], b[key]);
    if (compared === 1 || compared === -1) {
      // Nulls stay last regardless of direction.
      if (a[key] === null || b[key] === null) return compared;
      return sign * compared;
    }
    return 0;
  });
}

export interface HeatmapCell {
  row: number;
  col: number;
  runId: string | null;
  value: number | null;
}

export interface Heatmap {
  rows: Array<number | string>;
  cols: Array<number | string>;
  cells: HeatmapCell[][];
  min: number | null;
  max: number | null;
}

function sameValue(a: unknown, b: unknown): boolean {
  return String(a) === String(b);
}

/**
 * Map child runs onto a 2-D grid. The axes' own value order is preserved, so
 * the heatmap reads as the sweep definition rather than a ranking.
 */
export function buildHeatmap(
  rowAxis: ExperimentAxis,
  colAxis: ExperimentAxis,
  rows: VariantRow[],
  metric: VariantMetric,
): Heatmap {
  const values: number[] = [];
  const cells: HeatmapCell[][] = rowAxis.values.map((rowValue, rowIndex) =>
    colAxis.values.map((colValue, colIndex) => {
      const run = rows.find(
        (item) =>
          sameValue(item.values[rowAxis.path], rowValue) &&
          sameValue(item.values[colAxis.path], colValue),
      );
      const value = run ? variantMetricValue(run, metric) : null;
      if (value !== null) values.push(value);
      return {
        row: rowIndex,
        col: colIndex,
        runId: run?.run_id ?? null,
        value,
      };
    }),
  );
  return {
    rows: [...rowAxis.values],
    cols: [...colAxis.values],
    cells,
    min: values.length > 0 ? Math.min(...values) : null,
    max: values.length > 0 ? Math.max(...values) : null,
  };
}

/** Single-hue magnitude scale: colour encodes the metric only, not a winner. */
export function heatmapColour(value: number | null, min: number | null, max: number | null): string {
  if (value === null || min === null || max === null) return "transparent";
  if (max === min) return "rgba(86,180,233,0.30)";
  const ratio = Math.max(0, Math.min(1, (value - min) / (max - min)));
  return `rgba(86,180,233,${(0.08 + ratio * 0.5).toFixed(3)})`;
}

export function formatMetricValue(value: number | null, metric: VariantMetric): string {
  if (value === null) return "not available";
  if (metric === "net_pnl_usd" || metric === "gross_pnl_usd") {
    return fmtUsd(value, { signed: true });
  }
  if (metric === "net_return_pct" || metric === "max_drawdown_pct") {
    return fmtPct(value, 2, { signed: metric === "net_return_pct" });
  }
  if (metric === "trade_count") return fmtInteger(value);
  return fmtNumber(value, 2);
}

function formatAxisValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "not available";
  return String(value);
}

interface DetailState {
  loading: boolean;
  error: unknown | null;
  data: ExperimentDetail | null;
}

function SortHeader({
  label,
  sortKey,
  sort,
  onSort,
  align = "left",
}: {
  label: string;
  sortKey: VariantSortKey;
  sort: { key: VariantSortKey; direction: SortDirection };
  onSort: (key: VariantSortKey) => void;
  align?: "left" | "right";
}) {
  const active = sort.key === sortKey;
  return (
    <th
      scope="col"
      className={`table-head px-2 py-1 ${align === "right" ? "text-right" : "text-left"}`}
      aria-sort={active ? (sort.direction === "asc" ? "ascending" : "descending") : "none"}
    >
      <button
        type="button"
        className="btn-ghost px-0 text-[10px] font-semibold uppercase tracking-[0.06em]"
        onClick={() => onSort(sortKey)}
        title={`Sort by ${label}`}
      >
        {label}
        {active ? <span aria-hidden="true"> {sort.direction === "asc" ? "▲" : "▼"}</span> : null}
      </button>
    </th>
  );
}

/**
 * Bounded one- or two-axis sweep. The parent experiment owns standard child
 * runs; it never auto-promotes a winner and never hides a failed variant.
 */
export function SweepsPanel() {
  const experiments = useApiResource((signal) => api.experiments(signal), []);
  const strategies = useApiResource((signal) => api.strategies(signal), []);
  const instruments = useApiResource((signal) => api.instruments({ limit: 500 }, signal), []);

  const [name, setName] = useState("EMA parameter sweep");
  const [builtinId, setBuiltinId] = useState("nautilus_ema_cross");
  const [instrumentId, setInstrumentId] = useState("");
  const [axisOneParam, setAxisOneParam] = useState("fast_period");
  const [axisOneValues, setAxisOneValues] = useState("8, 12, 16");
  const [secondAxis, setSecondAxis] = useState(false);
  const [axisTwoParam, setAxisTwoParam] = useState("slow_period");
  const [axisTwoValues, setAxisTwoValues] = useState("20, 30, 40");
  const [fromText, setFromText] = useState("");
  const [toText, setToText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<unknown | null>(null);
  const [createdMessage, setCreatedMessage] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string[]>([]);
  const [details, setDetails] = useState<Record<string, DetailState>>({});
  const [cancelling, setCancelling] = useState<string | null>(null);
  const [cancelError, setCancelError] = useState<unknown | null>(null);
  const [heatMetric, setHeatMetric] = useState<VariantMetric>("net_pnl_usd");
  const [sort, setSort] = useState<{ key: VariantSortKey; direction: SortDirection }>({
    key: "variant",
    direction: "asc",
  });

  const builtins: BuiltinStrategy[] = strategies.data?.builtins ?? [];
  const builtin = builtins.find((item) => item.id === builtinId) ?? builtins[0] ?? null;
  const parameters = builtin?.parameters ?? [];
  // The catalogue may not contain the initially selected parameter; fall back
  // to the builtin's own order rather than posting a path it cannot resolve.
  const effectiveAxisOneParam = parameters.some((parameter) => parameter.name === axisOneParam)
    ? axisOneParam
    : parameters[0]?.name ?? "";
  const effectiveAxisTwoParam = parameters.some((parameter) => parameter.name === axisTwoParam)
    ? axisTwoParam
    : parameters[1]?.name ?? parameters[0]?.name ?? "";
  const items: Instrument[] = instruments.data?.instruments ?? [];

  const defaultInstrument = useMemo(
    () =>
      items.reduce<Instrument | null>(
        (best, item) => ((item.trade_count ?? 0) > (best?.trade_count ?? 0) ? item : best),
        null,
      ),
    [items],
  );
  const chosenInstrument =
    items.find((item) => item.instrument_id === instrumentId) ?? defaultInstrument;
  const effectiveInstrument = instrumentId || chosenInstrument?.instrument_id || "";

  // Dates default to the instrument's recorded coverage. Nothing is inferred.
  const coverageFrom = isoDateOnly(chosenInstrument?.coverage?.from ?? chosenInstrument?.first_ts);
  const coverageTo = isoDateOnly(chosenInstrument?.coverage?.to ?? chosenInstrument?.last_ts);
  const from = fromText || coverageFrom;
  const to = toText || coverageTo;

  const firstAxis = useMemo(() => {
    const parsed = parseAxisValues(axisOneValues);
    return {
      path: effectiveAxisOneParam ? `strategy.builtin_params.${effectiveAxisOneParam}` : "",
      values: parsed.values,
      invalid: parsed.invalid,
    };
  }, [effectiveAxisOneParam, axisOneValues]);

  const second = useMemo(() => {
    if (!secondAxis) return null;
    const parsed = parseAxisValues(axisTwoValues);
    return {
      path: effectiveAxisTwoParam ? `strategy.builtin_params.${effectiveAxisTwoParam}` : "",
      values: parsed.values,
      invalid: parsed.invalid,
    };
  }, [secondAxis, effectiveAxisTwoParam, axisTwoValues]);

  const duplicateAxis = !!second && second.path !== "" && second.path === firstAxis.path;
  const axes = [firstAxis, second].filter(
    (axis): axis is NonNullable<typeof axis> => !!axis && axis.path !== "" && !duplicateAxis,
  );
  const variants = variantCount(axes);
  const overCap = variants > MAX_SWEEP_VARIANTS;
  const invalidTokens = [
    ...firstAxis.invalid,
    ...(second?.invalid ?? []),
  ];

  const canSubmit =
    !!builtin &&
    !!firstAxis.path &&
    firstAxis.values.length > 0 &&
    !duplicateAxis &&
    (!second || (second.path !== "" && second.values.length > 0)) &&
    !!effectiveInstrument &&
    !!from &&
    !!to &&
    from <= to &&
    variants > 0 &&
    !overCap &&
    !submitting;

  const experimentList: Experiment[] = experiments.data?.experiments ?? [];

  const loadDetail = useCallback(async (id: string) => {
    setDetails((current) => ({
      ...current,
      [id]: { loading: true, error: null, data: current[id]?.data ?? null },
    }));
    try {
      const raw = await api.experiment(id);
      setDetails((current) => ({
        ...current,
        [id]: { loading: false, error: null, data: parseExperimentDetail(raw) },
      }));
    } catch (caught) {
      setDetails((current) => ({
        ...current,
        [id]: { loading: false, error: caught, data: current[id]?.data ?? null },
      }));
    }
  }, []);

  const toggleExpanded = (id: string) => {
    if (expanded.includes(id)) {
      setExpanded((current) => current.filter((item) => item !== id));
      return;
    }
    setExpanded((current) => [...current, id]);
    if (!details[id]?.data) void loadDetail(id);
  };

  const submit = async () => {
    if (!builtin || !canSubmit) return;
    setSubmitting(true);
    setError(null);
    setCreatedMessage(null);
    try {
      const builtinDefaults: Record<string, number | boolean | string> = {
        ...(builtin.defaults ?? {}),
      };
      for (const parameter of parameters) {
        if (!(parameter.name in builtinDefaults) && typeof parameter.default !== "undefined") {
          builtinDefaults[parameter.name] = parameter.default;
        }
      }
      const payload = buildExperimentRequest({
        name,
        builtinId: builtin.id,
        builtinDefaults,
        instrumentId: effectiveInstrument,
        from,
        to,
        axes: axes.map((axis) => ({ path: axis.path, values: axis.values })),
      });
      await api.createExperiment(payload);
      setCreatedMessage(
        `${variantCount(axes)} variant(s) queued. Every variant is retained; the weakest evidence governs.`,
      );
      experiments.reload();
    } catch (caught) {
      setError(caught);
    } finally {
      setSubmitting(false);
    }
  };

  const cancel = async (id: string) => {
    setCancelling(id);
    setCancelError(null);
    try {
      await api.cancelExperiment(id);
      experiments.reload();
      if (details[id]?.data) void loadDetail(id);
    } catch (caught) {
      setCancelError(caught);
    } finally {
      setCancelling(null);
    }
  };

  const selectBuiltin = (id: string) => {
    setBuiltinId(id);
    const next = builtins.find((item) => item.id === id) ?? null;
    const nextParameters = next?.parameters ?? [];
    const first = nextParameters[0] ?? null;
    const secondParameter = nextParameters[1] ?? null;
    setAxisOneParam(first?.name ?? "");
    setAxisOneValues(defaultSweepValues(first).join(", "));
    setAxisTwoParam(secondParameter?.name ?? "");
    setAxisTwoValues(defaultSweepValues(secondParameter).join(", "));
  };

  const toggleSort = (key: VariantSortKey) => {
    setSort((current) =>
      current.key === key
        ? { key, direction: current.direction === "asc" ? "desc" : "asc" }
        : {
            key,
            direction: key === "variant" || key === "status" ? "asc" : "desc",
          },
    );
  };

  const renderDetail = (experiment: Experiment) => {
    const state = details[experiment.id];
    if (!state || (state.loading && !state.data)) {
      return <LoadingState label="Loading experiment variants" rows={3} />;
    }
    if (state.error) {
      return (
        <ErrorState
          error={state.error}
          title="Experiment variants unavailable"
          onRetry={() => void loadDetail(experiment.id)}
        />
      );
    }
    const detail = state.data;
    if (!detail) return null;
    const rows = buildVariantRows(detail.axes, detail.runs);
    const sorted = sortVariantRows(rows, sort.key, sort.direction, detail.axes);
    const completed = detail.completed ?? rows.filter((row) =>
      ["complete", "failed", "cancelled", "orphaned"].includes(row.status),
    ).length;
    const total = detail.total ?? rows.length;
    const heatAxes = detail.axes.length === 2 ? detail.axes : null;
    const heatmap = heatAxes
      ? buildHeatmap(heatAxes[0], heatAxes[1], rows, heatMetric)
      : null;

    return (
      <div className="space-y-2 border-t border-line bg-surface-sunken/30 p-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="badge">
            {fmtInteger(completed)} / {fmtInteger(total)} complete
          </span>
          <span className="badge">{fmtInteger(total)} variants</span>
          {detail.axes.map((axis) => (
            <span key={axis.path} className="chip text-[10px]">
              {axisLabel(axis.path)} · {axis.values.length} value(s)
            </span>
          ))}
          <button
            type="button"
            className="btn-ghost ml-auto text-[10px]"
            onClick={() => void loadDetail(experiment.id)}
            disabled={state.loading}
          >
            {state.loading ? "Refreshing…" : "Refresh variants"}
          </button>
        </div>

        <p className="rounded border border-line bg-surface-raised px-2 py-1 text-[10px] leading-relaxed text-ink-muted">
          <strong className="font-semibold text-ink">Weakest evidence governs.</strong> A sweep is
          a set of ordinary backtests, not an optimiser. Every variant is retained, colour and
          sorting rank nothing, and no variant is promoted as a winner.
        </p>

        {heatmap && heatAxes ? (
          <div className="space-y-1">
            <div className="flex flex-wrap items-end gap-2">
              <label>
                <span className="label">Heatmap metric</span>
                <select
                  className="input mt-1 w-auto"
                  value={heatMetric}
                  onChange={(event) => setHeatMetric(event.target.value as VariantMetric)}
                  aria-label="Heatmap metric"
                >
                  {VARIANT_METRICS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
              <div className="min-w-[180px]">
                <span className="label">Colour scale</span>
                <div
                  className="mt-1 h-2 w-full rounded border border-line"
                  style={{
                    background:
                      "linear-gradient(to right, rgba(86,180,233,0.08), rgba(86,180,233,0.58))",
                  }}
                  role="img"
                  aria-label={`Colour scale from ${formatMetricValue(
                    heatmap.min,
                    heatMetric,
                  )} to ${formatMetricValue(heatmap.max, heatMetric)}`}
                />
                <div className="numeric mt-0.5 flex justify-between text-[9px] text-ink-faint">
                  <span>{formatMetricValue(heatmap.min, heatMetric)}</span>
                  <span>{formatMetricValue(heatmap.max, heatMetric)}</span>
                </div>
              </div>
              <span className="text-[10px] text-ink-faint">
                Rows {axisLabel(heatAxes[0].path)} · columns {axisLabel(heatAxes[1].path)}
              </span>
            </div>
            <div className="overflow-auto">
              <table
                className="border-collapse text-[10px]"
                data-testid="sweep-heatmap"
                aria-label="Variant heatmap"
              >
                <caption className="sr-only">
                  Two-axis sweep heatmap. Colour encodes {heatMetric}; it does not rank variants.
                </caption>
                <thead>
                  <tr>
                    <th scope="col" className="table-head px-2 py-1 text-left">
                      {axisLabel(heatAxes[0].path)} ↓ / {axisLabel(heatAxes[1].path)} →
                    </th>
                    {heatmap.cols.map((col) => (
                      <th key={String(col)} scope="col" className="table-head px-2 py-1 text-right">
                        {formatAxisValue(col)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {heatmap.rows.map((rowValue, rowIndex) => (
                    <tr key={String(rowValue)} className="border-b border-line/60">
                      <th scope="row" className="px-2 py-1 text-left font-medium text-ink">
                        {formatAxisValue(rowValue)}
                      </th>
                      {heatmap.cells[rowIndex].map((cell) => (
                        <td
                          key={`${rowIndex}:${cell.col}`}
                          className="numeric px-2 py-1 text-right"
                          style={{ backgroundColor: heatmapColour(cell.value, heatmap.min, heatmap.max) }}
                          title={
                            cell.runId
                              ? `${cell.runId}: ${formatMetricValue(cell.value, heatMetric)}`
                              : "No run for this combination"
                          }
                        >
                          {cell.runId ? formatMetricValue(cell.value, heatMetric) : "—"}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="text-[9px] text-ink-faint">
              Colour intensity is the selected metric only. A darker cell is a larger value, not a
              better strategy.
            </p>
          </div>
        ) : null}

        <div className="overflow-auto">
          <table className="w-full min-w-[640px] border-collapse text-[11px]" data-testid="variants-table">
            <caption className="sr-only">Experiment variants</caption>
            <thead className="sticky top-0 bg-surface-raised">
              <tr className="border-b border-line">
                <SortHeader label="Variant" sortKey="variant" sort={sort} onSort={toggleSort} />
                <th scope="col" className="table-head px-2 py-1 text-left">Run</th>
                <SortHeader label="Status" sortKey="status" sort={sort} onSort={toggleSort} />
                <SortHeader
                  label="Gross P&L"
                  sortKey="gross_pnl_usd"
                  sort={sort}
                  onSort={toggleSort}
                  align="right"
                />
                <SortHeader
                  label="Net P&L"
                  sortKey="net_pnl_usd"
                  sort={sort}
                  onSort={toggleSort}
                  align="right"
                />
                <SortHeader
                  label="Net return"
                  sortKey="net_return_pct"
                  sort={sort}
                  onSort={toggleSort}
                  align="right"
                />
                <SortHeader
                  label="Trades"
                  sortKey="trade_count"
                  sort={sort}
                  onSort={toggleSort}
                  align="right"
                />
                <th scope="col" className="table-head px-2 py-1 text-right">Open</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((row) => (
                <tr key={row.run_id} className="border-b border-line/60 hover:bg-surface-sunken/50">
                  <td className="px-2 py-1 text-[10px] text-ink">
                    {detail.axes.length > 0
                      ? detail.axes
                          .map((axis) => `${axisLabel(axis.path)} ${formatAxisValue(row.values[axis.path])}`)
                          .join(" · ")
                      : "not available"}
                  </td>
                  <td className="numeric px-2 py-1 text-[10px] text-ink-muted" title={row.run_id}>
                    {row.run_id.length > 22 ? `${row.run_id.slice(0, 22)}…` : row.run_id}
                    {row.metrics_scope === "in_sample" ? (
                      <span className="block text-[9px] text-warn" title="Out-of-sample metrics stay hidden until an explicit reveal">
                        in-sample only
                      </span>
                    ) : null}
                  </td>
                  <td className="px-2 py-1"><StatusPill status={row.status} /></td>
                  <td className="numeric px-2 py-1 text-right">
                    {fmtUsd(row.gross_pnl_usd, { signed: true })}
                  </td>
                  <td className="numeric px-2 py-1 text-right">
                    {fmtUsd(row.net_pnl_usd, { signed: true })}
                  </td>
                  <td className="numeric px-2 py-1 text-right">
                    {fmtPct(row.net_return_pct, 2, { signed: true })}
                  </td>
                  <td className="numeric px-2 py-1 text-right">{fmtInteger(row.trade_count)}</td>
                  <td className="px-2 py-1 text-right">
                    <Link
                      className="btn-ghost text-[10px]"
                      to={`/?run=${encodeURIComponent(row.run_id)}`}
                      title={`Open run ${row.run_id}`}
                    >
                      Open
                    </Link>
                  </td>
                </tr>
              ))}
              {sorted.length === 0 ? (
                <tr>
                  <td colSpan={8} className="px-2 py-3 text-center text-[11px] text-ink-muted">
                    No child runs recorded for this experiment.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
        <SourceNote>
          Gross and net are simulation artifacts from stored data. Gross is after spread and
          slippage embedded in the fill prices, before fees; net subtracts the separate fee line
          under the run's effective execution profile. An absent metric reads “not available”. A
          child run whose split has not been revealed reports its in-sample slice only, labelled
          “in-sample only”; no out-of-sample figure is inferred.
        </SourceNote>
      </div>
    );
  };

  return (
    <section className="panel overflow-hidden" aria-label="Experiments and sweeps">
      <div className="panel-title">
        <span>Experiments</span>
        <span className="text-[10px] font-normal text-ink-faint">
          One or two axes · max {MAX_SWEEP_VARIANTS} variants · concurrency 1
        </span>
        <button type="button" className="btn-ghost ml-auto text-[10px]" onClick={experiments.reload}>
          Refresh
        </button>
      </div>

      <div className="grid gap-3 border-b border-line p-3 lg:grid-cols-[minmax(0,1fr)_420px]">
        <div className="grid gap-2 sm:grid-cols-2">
          <label>
            <span className="label">Name</span>
            <input className="input mt-1" value={name} onChange={(event) => setName(event.target.value)} />
          </label>
          <label>
            <span className="label">Built-in</span>
            <select
              className="input mt-1"
              value={builtin?.id ?? ""}
              onChange={(event) => selectBuiltin(event.target.value)}
            >
              {builtins.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <label className="sm:col-span-2">
            <span className="label">Instrument</span>
            <select
              className="input mt-1"
              value={effectiveInstrument}
              onChange={(event) => setInstrumentId(event.target.value)}
              aria-label="Sweep instrument"
            >
              {items.slice(0, 200).map((item) => (
                <option key={item.instrument_id} value={item.instrument_id}>
                  {instrumentLabel(item)} ({fmtInteger(item.trade_count)} fills)
                </option>
              ))}
            </select>
            <span className="mt-0.5 block text-[9px] text-ink-faint">
              Defaults to the instrument with the most recorded fills.
            </span>
          </label>

          <label>
            <span className="label">Axis 1 parameter</span>
            <select
              className="input mt-1"
              value={effectiveAxisOneParam}
              onChange={(event) => setAxisOneParam(event.target.value)}
              disabled={parameters.length === 0}
              aria-label="First axis parameter"
            >
              {parameters.map((parameter) => (
                <option key={parameter.name} value={parameter.name}>
                  {parameter.label ?? parameter.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span className="label">Axis 1 values (comma separated)</span>
            <input
              className="input mt-1"
              value={axisOneValues}
              onChange={(event) => setAxisOneValues(event.target.value)}
              aria-label="First axis values"
            />
          </label>

          <div className="sm:col-span-2 flex items-center gap-2">
            <input
              id="sweep-second-axis"
              type="checkbox"
              checked={secondAxis}
              onChange={(event) => setSecondAxis(event.target.checked)}
            />
            <label htmlFor="sweep-second-axis" className="text-[11px] text-ink">
              Add a second axis (2-D heatmap)
            </label>
          </div>

          {secondAxis ? (
            <>
              <label>
                <span className="label">Axis 2 parameter</span>
                <select
                  className="input mt-1"
                  value={effectiveAxisTwoParam}
                  onChange={(event) => setAxisTwoParam(event.target.value)}
                  disabled={parameters.length < 2}
                  aria-label="Second axis parameter"
                >
                  {parameters.map((parameter) => (
                    <option key={parameter.name} value={parameter.name}>
                      {parameter.label ?? parameter.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span className="label">Axis 2 values (comma separated)</span>
                <input
                  className="input mt-1"
                  value={axisTwoValues}
                  onChange={(event) => setAxisTwoValues(event.target.value)}
                  aria-label="Second axis values"
                />
              </label>
            </>
          ) : null}

          <label>
            <span className="label">From</span>
            <input
              type="date"
              className="input mt-1"
              value={from}
              onChange={(event) => setFromText(event.target.value)}
              aria-label="Sweep from date"
            />
          </label>
          <label>
            <span className="label">To</span>
            <input
              type="date"
              className="input mt-1"
              value={to}
              onChange={(event) => setToText(event.target.value)}
              aria-label="Sweep to date"
            />
          </label>
          <p className="sm:col-span-2 text-[9px] text-ink-faint">
            Dates default to the instrument's recorded coverage
            {coverageFrom && coverageTo ? ` (${coverageFrom} → ${coverageTo})` : ""}. Edit either
            field to override; a missing range is not inferred.
          </p>

          <div className="sm:col-span-2 flex flex-wrap items-center gap-2">
            <button type="button" className="btn-primary" disabled={!canSubmit} onClick={() => void submit()}>
              {submitting ? "Creating…" : `Create sweep (${variants} variants)`}
            </button>
            <span
              className={
                overCap ? "badge-danger" : variants > 0 ? "badge" : "badge-warn"
              }
              role="status"
            >
              {variants} / {MAX_SWEEP_VARIANTS} variants
            </span>
            <span className="text-[10px] text-ink-faint">
              One parent, standard child runs. Every variant is retained.
            </span>
          </div>
          {duplicateAxis ? (
            <p className="sm:col-span-2 text-[10px] text-negative" role="alert">
              The two axes must name different parameters.
            </p>
          ) : null}
          {overCap ? (
            <p className="sm:col-span-2 text-[10px] text-negative" role="alert">
              {variants} variants exceeds the {MAX_SWEEP_VARIANTS}-variant cap. Remove values or a
              whole axis.
            </p>
          ) : null}
          {from && to && from > to ? (
            <p className="sm:col-span-2 text-[10px] text-negative" role="alert">
              The from date must be on or before the to date.
            </p>
          ) : null}
          {invalidTokens.length > 0 ? (
            <p className="sm:col-span-2 text-[10px] text-warn" role="status">
              Ignored non-numeric value(s): {invalidTokens.join(", ")}.
            </p>
          ) : null}
          {error ? (
            <div className="sm:col-span-2">
              <ErrorState error={error} title="Sweep could not be created" />
            </div>
          ) : null}
          {createdMessage ? (
            <p className="sm:col-span-2 text-[10px] text-positive" role="status">
              {createdMessage}
            </p>
          ) : null}
        </div>

        <div className="overflow-auto rounded border border-line">
          {experiments.loading ? (
            <LoadingState rows={4} label="Loading experiments" />
          ) : experiments.error ? (
            <ErrorState error={experiments.error} title="Experiments unavailable" onRetry={experiments.reload} />
          ) : experimentList.length === 0 ? (
            <p className="p-3 text-[11px] text-ink-muted">No experiments yet.</p>
          ) : (
            <>
              <table className="w-full text-[11px]">
                <caption className="sr-only">Experiments</caption>
                <thead className="sticky top-0 bg-surface-raised">
                  <tr className="border-b border-line">
                    <th className="table-head px-2 py-1 text-left">Experiment</th>
                    <th className="table-head px-2 py-1 text-left">Status</th>
                    <th className="table-head px-2 py-1 text-right">Complete</th>
                    <th className="table-head px-2 py-1 text-right">Variants</th>
                    <th className="table-head px-2 py-1" />
                  </tr>
                </thead>
                <tbody>
                  {experimentList.map((experiment) => {
                    const isOpen = expanded.includes(experiment.id);
                    const completed = experiment.completed ?? 0;
                    const total = experiment.total ?? null;
                    const pct = total && total > 0 ? Math.min(100, (completed / total) * 100) : 0;
                    const active = experiment.status === "queued" || experiment.status === "running";
                    return (
                      <Fragment key={experiment.id}>
                        <tr className="border-b border-line/60">
                          <td className="px-2 py-1">
                            <button
                              type="button"
                              className="btn-ghost px-0 text-left text-[10px]"
                              onClick={() => toggleExpanded(experiment.id)}
                              aria-expanded={isOpen}
                              aria-label={`${isOpen ? "Collapse" : "Expand"} experiment ${experiment.name ?? experiment.id}`}
                            >
                              <span aria-hidden="true">{isOpen ? "▾" : "▸"}</span>{" "}
                              {experiment.name ?? experiment.id}
                            </button>
                            <span className="numeric block text-[9px] text-ink-faint" title={experiment.id}>
                              {experiment.id} · {fmtTimestamp(experiment.created_at, false)}
                            </span>
                          </td>
                          <td className="px-2 py-1"><StatusPill status={experiment.status} /></td>
                          <td className="numeric px-2 py-1 text-right">
                            <span>
                              {fmtInteger(experiment.completed)} / {fmtInteger(total)}
                            </span>
                            <div
                              className="mt-0.5 h-1 w-16 bg-surface-sunken"
                              role="progressbar"
                              aria-valuemin={0}
                              aria-valuemax={total ?? 100}
                              aria-valuenow={completed}
                              aria-label={`${completed} of ${total ?? "unknown"} variants complete`}
                            >
                              <div className="h-1 bg-accent" style={{ width: `${pct}%` }} />
                            </div>
                          </td>
                          <td className="numeric px-2 py-1 text-right">
                            <span className="badge">{fmtInteger(total)}</span>
                          </td>
                          <td className="px-2 py-1 text-right">
                            {active ? (
                              <button
                                type="button"
                                className="btn-ghost text-[10px]"
                                onClick={() => void cancel(experiment.id)}
                                disabled={cancelling === experiment.id}
                                title="Cancel the parent and its unfinished children"
                              >
                                {cancelling === experiment.id ? "Cancelling…" : "Cancel"}
                              </button>
                            ) : (
                              <span className="text-[9px] text-ink-faint">terminal</span>
                            )}
                          </td>
                        </tr>
                        {isOpen ? (
                          <tr>
                            <td colSpan={5}>{renderDetail(experiment)}</td>
                          </tr>
                        ) : null}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
              {cancelError ? (
                <ErrorState error={cancelError} title="Cancellation failed" />
              ) : null}
            </>
          )}
        </div>
      </div>

      <div className="border-t border-line px-2 py-1">
        <SourceNote>
          Experiments are bounded sweeps, not optimisers: one or two axes, at most{" "}
          {MAX_SWEEP_VARIANTS} variants, concurrency 1, every variant retained. Cancelling the
          parent cancels its unfinished children. Progress is completed / total, and the weakest
          evidence level governs any conclusion.
        </SourceNote>
      </div>
    </section>
  );
}
