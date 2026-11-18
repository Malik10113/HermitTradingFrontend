/**
 * The backend emits the wire contract plus a few compact aliases (`items`,
 * `summary`, `first_ts`, `available_from_utc`, …). Normalise once here so the
 * rest of the UI sees one canonical shape; never infer a value that is absent.
 */
import type {
  BarsResponse,
  BuiltinStrategy,
  CompareResponse,
  CostBreakdown,
  Diagnostics,
  EffectiveExecution,
  EquityPoint,
  HoldoutState,
  IndicatorParameter,
  IndicatorSpec,
  Instrument,
  InstrumentListResponse,
  InstrumentQuality,
  LifecycleMeta,
  LifecyclePhase,
  Metrics,
  PhaseMetric,
  Preset,
  PresetListResponse,
  RunDetail,
  RunListResponse,
  SavedStrategy,
  SearchResponse,
  StrategiesResponse,
  Trade,
} from "./types";

function record(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function array(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function str(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function bool(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

export function tokenOf(instrument: Partial<Instrument> | null | undefined): "YES" | "NO" | null {
  if (!instrument) return null;
  if (instrument.token_index === 0) return "YES";
  if (instrument.token_index === 1) return "NO";
  if (instrument.selection === "YES" || instrument.selection === "NO") return instrument.selection;
  if (instrument.instrument_id?.endsWith("-YES.POLYMARKET")) return "YES";
  if (instrument.instrument_id?.endsWith("-NO.POLYMARKET")) return "NO";
  return null;
}

export function instrumentLabel(instrument: Partial<Instrument> | null | undefined): string {
  if (!instrument) return "No instrument";
  const base =
    instrument.question ??
    (instrument.home_name && instrument.away_name
      ? `${instrument.home_name} vs ${instrument.away_name}`
      : null) ??
    instrument.market_slug ??
    instrument.instrument_id ??
    "Instrument";
  const token = tokenOf(instrument);
  return token ? `${base} · ${token}` : base;
}

export function normaliseInstrument(raw: unknown): Instrument {
  const source = record(raw);
  const token = tokenOf(source as Partial<Instrument>);
  const settlement = record(source.settlement);
  const settlementValue =
    num(source.settlement_value) ??
    num(token === "NO" ? settlement.no_settlement : settlement.yes_settlement);
  const precision = num(source.price_precision);
  const coverage = record(source.coverage);
  const from = str(coverage.from) ?? str(source.first_ts);
  const to = str(coverage.to) ?? str(source.last_ts);

  return {
    ...(source as unknown as Instrument),
    selection: token ?? (str(source.selection) as Instrument["selection"]),
    token_index: num(source.token_index),
    price_precision: precision,
    price_tick:
      num(source.price_tick) ?? (precision != null ? Number(`1e-${precision}`) : null),
    first_ts: str(source.first_ts),
    last_ts: str(source.last_ts),
    coverage: from || to ? { from, to } : null,
    settlement: Object.keys(settlement).length > 0 ? (settlement as Instrument["settlement"]) : null,
    settlement_value: settlementValue as 0 | 1 | null,
    settlement_known:
      bool(source.settlement_known) ?? (settlementValue != null ? true : null),
    settled_ts: str(source.settled_ts) ?? str(settlement.settled_ts_utc),
    lifecycle: Object.keys(record(source.lifecycle)).length > 0
      ? (record(source.lifecycle) as unknown as LifecycleMeta)
      : null,
  };
}

export function normaliseInstrumentList(raw: unknown): InstrumentListResponse {
  const source = record(raw);
  const items = Array.isArray(raw)
    ? raw
    : array(source.items ?? source.instruments);
  return {
    instruments: items.map(normaliseInstrument),
    total: num(source.total) ?? items.length,
    has_more: bool(source.has_more) ?? false,
  };
}

export function normaliseSearch(raw: unknown): SearchResponse {
  const source = record(raw);
  const items = array(source.items ?? source.instruments ?? source.results);
  return {
    results: items.map((item) => {
      const hit = normaliseInstrument(item);
      return {
        instrument_id: hit.instrument_id,
        label: instrumentLabel(hit),
        league: hit.league ?? null,
        match_key: hit.match_key ?? null,
        selection: tokenOf(hit),
        score: num(record(item).score),
        coverage: hit.coverage ?? null,
      };
    }),
    total: num(source.total) ?? items.length,
  };
}

export function normaliseQuality(raw: unknown): InstrumentQuality {
  const source = record(raw);
  const warnings = array(source.warnings).map((warning) =>
    typeof warning === "string"
      ? warning
      : str(record(warning).message) ?? str(record(warning).code) ?? "data warning",
  );
  const gaps = Array.isArray(source.gaps)
    ? array(source.gaps).map((gap) => {
        const entry = record(gap);
        return {
          from: str(entry.from) ?? "",
          to: str(entry.to) ?? "",
          duration_s: num(entry.duration_s),
        };
      })
    : null;
  const longestGapMinutes = num(source.longest_gap_minutes);
  return {
    instrument_id: str(source.instrument_id) ?? "",
    available_from: str(source.available_from) ?? str(source.available_from_utc),
    available_to: str(source.available_to) ?? str(source.available_to_utc),
    trade_count: num(source.trade_count),
    bar_count: num(source.bar_count),
    active_minutes: num(source.active_minutes),
    active_minute_pct: num(source.active_minute_pct) ?? num(source.active_minutes_pct),
    longest_gap_s:
      num(source.longest_gap_s) ?? (longestGapMinutes != null ? longestGapMinutes * 60 : null),
    median_trade_usd:
      num(source.median_trade_usd) ?? num(source.median_trade_size_usd),
    quality_grade: str(source.quality_grade),
    warnings,
    gaps,
    settlement_known: bool(source.settlement_known),
    settlement_value: num(source.settlement_value) as 0 | 1 | null,
    data_version: str(source.data_version),
  };
}

export function normaliseParameter(raw: unknown): IndicatorParameter {
  const source = record(raw);
  return {
    ...(source as unknown as IndicatorParameter),
    name: str(source.name) ?? str(source.key) ?? "parameter",
  };
}

export function normaliseIndicatorSpecs(raw: unknown): IndicatorSpec[] {
  const source = record(raw);
  const items = Array.isArray(raw) ? raw : array(source.indicators);
  return items.map((item) => {
    const spec = record(item);
    return {
      ...(spec as unknown as IndicatorSpec),
      id: str(spec.id) ?? "",
      label: str(spec.label) ?? str(spec.id) ?? "Indicator",
      pane: spec.pane === "separate" ? "separate" : "overlay",
      parameters: array(spec.parameters ?? spec.params).map(normaliseParameter),
      outputs: (spec.outputs as IndicatorSpec["outputs"]) ?? ["value"],
      warmup: num(spec.warmup),
      description: str(spec.description),
    };
  });
}

export function normaliseBuiltin(raw: unknown): BuiltinStrategy {
  const source = record(raw);
  const parameters = array(source.parameters ?? source.params).map(normaliseParameter);
  return {
    ...(source as unknown as BuiltinStrategy),
    id: str(source.id) ?? "",
    label: str(source.label) ?? str(source.id) ?? "Built-in",
    parameters,
    params: parameters,
  } as BuiltinStrategy;
}

export function normaliseStrategies(raw: unknown): StrategiesResponse {
  if (Array.isArray(raw)) {
    const builtins = raw
      .filter((item) => {
        const entry = record(item);
        return !!entry.id && !("strategy" in entry) && !("config" in entry);
      })
      .map(normaliseBuiltin);
    const saved = raw
      .filter((item) => {
        const entry = record(item);
        return !!("strategy" in entry || "config" in entry);
      })
      .map(normaliseSavedStrategy);
    return { builtins, saved };
  }
  const source = record(raw);
  return {
    builtins: array(source.builtins ?? source.strategies).map(normaliseBuiltin),
    saved: array(source.saved).map(normaliseSavedStrategy),
  };
}

function normaliseSavedStrategy(raw: unknown): SavedStrategy {
  const source = record(raw);
  return {
    id: str(source.id) ?? "",
    label: str(source.label) ?? str(source.name) ?? "Saved strategy",
    strategy: (source.strategy ?? source.config) as SavedStrategy["strategy"],
    created_at: str(source.created_at),
    updated_at: str(source.updated_at),
  };
}

export function normaliseMetrics(raw: unknown): Metrics | null {
  const source = record(raw);
  if (Object.keys(source).length === 0) return null;
  const grossSource = record(source.gross);
  const netSource = record(source.net);
  const metrics: Metrics = {
    ...(source as Metrics),
    gross: Object.keys(grossSource).length > 0 ? (grossSource as Metrics) : undefined,
    net: Object.keys(netSource).length > 0 ? (netSource as Metrics) : undefined,
  };
  const minutes = num(source.avg_holding_minutes);
  metrics.avg_entry_probability =
    num(source.avg_entry_probability) ?? num(source.avg_entry_prob);
  metrics.avg_exit_probability =
    num(source.avg_exit_probability) ?? num(source.avg_exit_prob);
  metrics.avg_probability_move_pts =
    num(source.avg_probability_move_pts) ?? num(source.avg_yes_points_captured);
  metrics.avg_holding_period_s =
    num(source.avg_holding_period_s) ?? (minutes != null ? minutes * 60 : null);
  metrics.net_return_pct = num(source.net_return_pct) ?? num(source.return_pct);
  metrics.gross_return_pct = num(source.gross_return_pct);
  metrics.sharpe = num(source.sharpe);
  metrics.max_drawdown_pct = num(source.max_drawdown_pct);
  metrics.trade_count = num(source.trade_count);
  metrics.win_rate = num(source.win_rate);
  metrics.profit_factor = num(source.profit_factor);
  metrics.expectancy_usd = num(source.expectancy_usd);
  metrics.fees_usd = num(source.fees_usd);
  metrics.slippage_usd = num(source.slippage_usd);
  metrics.held_to_settlement_count = num(source.held_to_settlement_count);
  metrics.early_exit_count = num(source.early_exit_count);
  return metrics;
}

export function normaliseDiagnostics(raw: unknown): Diagnostics | null {
  const source = record(raw);
  if (Object.keys(source).length === 0) return null;
  const holdingMinutes = num(source.avg_holding_minutes);
  const signalMinutes = num(source.avg_minutes_between_signals);
  const warnings = array(source.warnings).map((warning) =>
    typeof warning === "string"
      ? warning
      : {
          code: str(record(warning).code) ?? "warning",
          message: str(record(warning).message) ?? "data warning",
        },
  );
  return {
    ...(source as Diagnostics),
    avg_holding_time_s:
      num(source.avg_holding_time_s) ??
      (holdingMinutes != null ? holdingMinutes * 60 : null),
    avg_time_between_signals_s:
      num(source.avg_time_between_signals_s) ??
      (signalMinutes != null ? signalMinutes * 60 : null),
    warnings,
  };
}

function normaliseTrade(raw: unknown, index: number): Trade {
  const source = record(raw);
  const token: "YES" | "NO" = source.token === "NO" ? "NO" : "YES";
  const payloadSide = source.side === "SELL" ? "SELL" : "BUY";
  const rawSide = str(source.side);
  const intent =
    str(source.intent) ??
    (rawSide === "short" ? "open_short" : rawSide === "long" ? "open_long" : null);
  let entryContext: Record<string, unknown> | null = null;
  if (typeof source.entry_context === "string") {
    try {
      entryContext = record(JSON.parse(source.entry_context));
    } catch {
      entryContext = null;
    }
  } else if (source.entry_context && typeof source.entry_context === "object") {
    entryContext = record(source.entry_context);
  }
  return {
    ...(source as unknown as Trade),
    trade_id: str(source.trade_id) ?? `T-${String(index + 1).padStart(4, "0")}`,
    side: payloadSide,
    close_side: source.close_side === "BUY" ? "BUY" : "SELL",
    token,
    intent,
    entry_ts: str(source.entry_ts) ?? "",
    exit_ts: str(source.exit_ts),
    entry_price: num(source.entry_price) ?? 0,
    exit_price: num(source.exit_price),
    contracts: num(source.contracts) ?? num(source.quantity),
    fees_usd: num(source.fees_usd),
    gross_pnl_usd: num(source.gross_pnl_usd),
    net_pnl_usd: num(source.net_pnl_usd),
    slippage_usd: num(source.slippage_usd),
    held_to_settlement:
      bool(source.held_to_settlement) ?? source.exit_reason === "settlement",
    phase:
      (str(source.phase) as LifecyclePhase | null) ??
      (str(source.entry_phase) as LifecyclePhase | null),
    entry_context: entryContext,
  };
}

export function normaliseTrades(raw: unknown): Trade[] {
  return array(raw).map(normaliseTrade);
}

export function normaliseEquity(raw: unknown): EquityPoint[] {
  return array(raw)
    .map((item) => {
      const source = record(item);
      const value = num(source.equity_usd) ?? num(source.equity_index);
      const ts = str(source.ts) ?? (num(source.ts_ns) != null
        ? new Date((source.ts_ns as number) / 1e6).toISOString()
        : null);
      return {
        ts: ts ?? "",
        equity_usd: value ?? Number.NaN,
        drawdown_pct: num(source.drawdown_pct),
      };
    })
    .filter((point) => point.ts && Number.isFinite(point.equity_usd));
}

export function normaliseCosts(raw: unknown): CostBreakdown | null {
  const source = record(raw);
  if (Object.keys(source).length === 0) return null;
  return {
    ...(source as CostBreakdown),
    fees_usd: num(source.fees_usd),
    spread_usd: num(source.spread_usd),
    impact_usd: num(source.impact_usd),
    slippage_usd: num(source.slippage_usd),
    total_cost_usd: num(source.total_cost_usd),
    total_cost_bps: num(source.total_cost_bps),
    fallback_rate: num(source.fallback_rate),
    requested_profile: str(source.requested_profile) as CostBreakdown["requested_profile"],
    effective_profile: str(source.effective_profile) as CostBreakdown["effective_profile"],
    fallback_reason: str(source.fallback_reason),
  };
}

export function normalisePhases(raw: unknown): PhaseMetric[] | null {
  if (Array.isArray(raw)) {
    return raw.map((item) => {
      const phase = record(item);
      return {
        phase: (str(phase.phase) ?? "unknown") as LifecyclePhase,
        trades: num(phase.trades),
        pnl_usd: num(phase.pnl_usd),
        return_pct: num(phase.return_pct),
        sharpe: num(phase.sharpe),
      };
    });
  }
  const source = record(raw);
  const entries = Object.entries(source);
  if (entries.length === 0) return null;
  return entries.map(([phase, value]) => {
    const metrics = record(value);
    return {
      phase: phase as LifecyclePhase,
      trades: num(metrics.trade_count) ?? num(metrics.trades),
      pnl_usd: num(metrics.net_pnl_usd) ?? num(metrics.pnl_usd),
      return_pct: num(metrics.return_pct),
      sharpe: num(metrics.sharpe),
    };
  });
}

export function normaliseHoldout(raw: unknown, split: unknown): HoldoutState | null {
  const source = record(raw);
  if (Object.keys(source).length === 0) return null;
  const revealCount = num(source.reveal_count) ?? 0;
  const warning = str(source.warning);
  const clean = bool(source.clean);
  return {
    revealed: bool(source.revealed) ?? revealCount > 0,
    reveal_count: revealCount,
    split: (source.split ?? split) as HoldoutState["split"],
    revealed_at: str(source.revealed_at),
    warning:
      warning ??
      (clean === false
        ? "This out-of-sample window has already been revealed; it is no longer clean."
        : null),
  };
}

export function normaliseExecution(raw: unknown, top: unknown = {}): EffectiveExecution | null {
  const source = record(raw);
  const summary = record(top);
  if (Object.keys(source).length === 0) {
    const requested = str(summary.requested_profile);
    const effective = str(summary.effective_profile);
    if (!requested && !effective) return null;
    return {
      requested: (requested ?? effective ?? "conservative") as EffectiveExecution["requested"],
      effective: (effective ?? requested ?? "conservative") as EffectiveExecution["effective"],
      fallback_reason: str(summary.fallback_reason),
    };
  }
  const coverage = record(source.book_coverage);
  const window = record(coverage.window);
  const startNs = num(window.start_ns);
  const endNs = num(window.end_ns);
  const costEstimate = record(source.cost_estimate);
  return {
    requested: (str(source.requested) ?? str(summary.requested_profile) ?? "conservative") as EffectiveExecution["requested"],
    effective: (str(source.effective) ?? str(summary.effective_profile) ?? "conservative") as EffectiveExecution["effective"],
    fallback_reason: str(source.fallback_reason) ?? str(summary.fallback_reason),
    cost_source: str(source.cost_source),
    fee_bps: num(source.fee_bps),
    slippage_bps: num(source.slippage_bps),
    min_abs_slippage: num(source.min_abs_slippage),
    book_coverage:
      Object.keys(coverage).length > 0
        ? {
            from:
              str(coverage.from) ??
              str(window.first_month) ??
              (startNs != null ? new Date(startNs / 1e6).toISOString() : null),
            to:
              str(coverage.to) ??
              str(window.last_month) ??
              (endNs != null ? new Date(endNs / 1e6).toISOString() : null),
            first_month: str(window.first_month),
            last_month: str(window.last_month),
            has_coverage: bool(coverage.has_coverage),
            snapshots: num(window.snapshots),
            reason: str(coverage.reason),
          }
        : null,
    cost_estimate:
      Object.keys(costEstimate).length > 0
        ? {
            fee_bps: num(costEstimate.fee_bps),
            slippage_bps: num(costEstimate.slippage_bps),
            min_abs_slippage: num(costEstimate.min_abs_slippage),
            per_side_bps:
              num(costEstimate.per_side_bps) ?? num(costEstimate.total_cost_bps),
            per_side_price_points:
              num(costEstimate.per_side_price_points) ?? num(costEstimate.slip_price_points),
            note: str(costEstimate.note),
          }
        : null,
  };
}

const TERMINAL = new Set(["complete", "failed", "cancelled", "orphaned"]);

export function normaliseRun(raw: unknown): RunDetail {
  const source = record(raw);
  const metricsSource = source.summary ?? source.metrics;
  const metrics = normaliseMetrics(metricsSource);
  const manifest = record(source.manifest);
  const execution = normaliseExecution(source.execution ?? manifest.execution, source);
  const config = record(source.config);
  const evaluation = record(config.evaluation);
  const status = (str(source.status) ?? "queued") as RunDetail["status"];
  const trades = source.trades != null ? normaliseTrades(source.trades) : null;
  const equitySource = source.equity ?? source.equity_index;
  const phasesSource = source.phases ?? source.phase_breakdown ?? manifest.phase_breakdown;
  const lifecycle = source.lifecycle ? (record(source.lifecycle) as unknown as LifecycleMeta) : null;
  const diagnostics = normaliseDiagnostics(source.diagnostics);

  return {
    ...(source as unknown as RunDetail),
    run_id: str(source.run_id) ?? "",
    status,
    created_at: str(source.created_at),
    started_at: str(source.started_at),
    completed_at:
      str(source.completed_at) ?? (TERMINAL.has(status) ? str(source.updated_at) : null),
    updated_at: str(source.updated_at),
    requested_profile: str(source.requested_profile) as RunDetail["requested_profile"],
    effective_profile: str(source.effective_profile) as RunDetail["effective_profile"],
    fallback_reason: str(source.fallback_reason),
    fallback_rate: num(source.fallback_rate),
    execution,
    metrics,
    diagnostics,
    costs: normaliseCosts(source.costs),
    phases: normalisePhases(phasesSource),
    holdout: normaliseHoldout(source.holdout, evaluation.split),
    lifecycle_version: str(source.lifecycle_version) ?? str(manifest.lifecycle_version),
    lifecycle,
    equity: equitySource != null ? normaliseEquity(equitySource) : null,
    trades,
    signals: Array.isArray(source.signals)
      ? (source.signals as RunDetail["signals"])
      : null,
    config: Object.keys(config).length > 0 ? (config as unknown as RunDetail["config"]) : null,
    warnings:
      (source.warnings as RunDetail["warnings"]) ??
      (diagnostics?.warnings ?? null),
    phase: (str(source.phase) as RunDetail["phase"]) ?? null,
  };
}

export function normaliseRunList(raw: unknown): RunListResponse {
  const source = record(raw);
  const items = array(source.runs ?? source.items);
  return { runs: items.map(normaliseRun), total: num(source.total) ?? items.length };
}

export function normaliseCompare(raw: unknown): CompareResponse {
  const source = record(raw);
  return { runs: array(source.runs).map(normaliseRun) };
}

export function normalisePresets(raw: unknown): PresetListResponse {
  const source = record(raw);
  const items = Array.isArray(raw) ? raw : array(source.items ?? source.presets);
  return {
    presets: items.map((item) => {
      const preset = record(item);
      return {
        id: str(preset.id) ?? "",
        name: str(preset.name) ?? "Preset",
        config: preset.config as Preset["config"],
        created_at: str(preset.created_at),
        updated_at: str(preset.updated_at),
      };
    }),
  };
}

export function normaliseBars(raw: unknown): BarsResponse {
  const source = record(raw);
  return {
    instrument_id: str(source.instrument_id) ?? "",
    bar_type: str(source.bar_type) ?? "",
    data_version: str(source.data_version),
    bars: array(source.bars) as BarsResponse["bars"],
    has_more: bool(source.has_more) ?? false,
  };
}

export type { RunSummary, StrategySection } from "./types";
