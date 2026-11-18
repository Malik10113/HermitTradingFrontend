/**
 * Wire types for the v1 API contract (docs/IMPLEMENTATION.md §"API contract").
 *
 * Units are part of every field name: `_usd` is USD notional
 * capital, `_bps` is basis points, `_pct` is a percentage (0–100), `_ns` is
 * UTC nanoseconds. Probabilities are 0–1. Timestamps are ISO-8601 UTC strings.
 *
 * Optional fields are optional because an artifact may be absent. The UI must
 * render "not available" rather than infer a value.
 */

export type ExecutionProfile =
  | "frictionless"
  | "optimistic"
  | "conservative"
  | "stress"
  | "book";

export const EXECUTION_PROFILES: ExecutionProfile[] = [
  "frictionless",
  "optimistic",
  "conservative",
  "stress",
  "book",
];

export type RunStatus =
  | "queued"
  | "running"
  | "complete"
  | "failed"
  | "cancelled"
  | "orphaned";

export type LifecyclePhase =
  | "pre_match"
  | "in_play"
  | "post_match"
  | "settled"
  | "unknown";

export type BarAlias =
  | "1m"
  | "5m"
  | "15m"
  | "1h"
  | "1d"
  | "tick_100"
  | "volume_1000"
  | "value_10000"
  | "renko_0.01";

export interface ApiErrorBody {
  code: string;
  message: string;
  details?: unknown;
}

export interface MetaResponse {
  data_version?: string | null;
  lifecycle_version?: string | null;
  code_version?: string | null;
  nautilus_version?: string | null;
  generated_at?: string | null;
  catalog?: {
    instruments?: number | null;
    coverage_from?: string | null;
    coverage_to?: string | null;
    bars?: number | null;
    [key: string]: unknown;
  } | null;
  [key: string]: unknown;
}

export interface CoverageWindow {
  from?: string | null;
  to?: string | null;
  first_month?: string | null;
  last_month?: string | null;
  has_coverage?: boolean | null;
  snapshots?: number | null;
  reason?: string | null;
}

export interface Instrument {
  instrument_id: string;
  venue?: string | null;
  league?: string | null;
  market_slug?: string | null;
  match_key?: string | null;
  question?: string | null;
  selection?: "YES" | "NO" | string | null;
  outcome?: string | null;
  condition_id?: string | null;
  end_date?: string | null;
  price_tick?: number | null;
  price_precision?: number | null;
  coverage?: CoverageWindow | null;
  first_ts?: string | null;
  last_ts?: string | null;
  event_date?: string | null;
  home_name?: string | null;
  away_name?: string | null;
  token_index?: number | null;
  trade_count?: number | null;
  bar_count?: number | null;
  quality_grade?: string | null;
  settlement_value?: 0 | 1 | null;
  settlement_known?: boolean | null;
  settled_ts?: string | null;
  settlement?: {
    yes_settlement?: 0 | 1 | null;
    no_settlement?: 0 | 1 | null;
    settled_ts_utc?: string | null;
    source?: string | null;
  } | null;
  lifecycle?: LifecycleMeta | null;
}

export interface InstrumentListResponse {
  instruments: Instrument[];
  total?: number | null;
  has_more?: boolean;
}

export interface InstrumentQuality {
  instrument_id: string;
  from?: string | null;
  to?: string | null;
  available_from?: string | null;
  available_to?: string | null;
  trade_count?: number | null;
  bar_count?: number | null;
  active_minutes?: number | null;
  active_minute_pct?: number | null;
  longest_gap_s?: number | null;
  median_trade_usd?: number | null;
  quality_grade?: string | null;
  warnings?: string[];
  gaps?: Array<{ from: string; to: string; duration_s?: number | null }> | null;
  settlement_known?: boolean | null;
  settlement_value?: 0 | 1 | null;
  data_version?: string | null;
}

export interface SearchHit {
  instrument_id: string;
  label?: string | null;
  league?: string | null;
  match_key?: string | null;
  selection?: string | null;
  score?: number | null;
  coverage?: CoverageWindow | null;
}

export interface SearchResponse {
  results: SearchHit[];
  total?: number | null;
}

export interface Bar {
  ts_event: string;
  ts_event_ns?: number | null;
  ts_init?: string | null;
  open: number;
  high: number;
  low: number;
  close: number;
  volume_usd?: number | null;
  trade_count?: number | null;
  is_stale?: boolean;
  source?: string | null;
  data_version?: string | null;
}

export interface BarsResponse {
  instrument_id: string;
  bar_type: string;
  data_version?: string | null;
  bars: Bar[];
  has_more?: boolean;
}

export type ParameterType = "int" | "integer" | "float" | "number" | "bool" | "boolean";

export interface IndicatorParameter {
  name: string;
  label?: string;
  type: ParameterType | string;
  min?: number | null;
  max?: number | null;
  default: number | boolean;
  step?: number | null;
}

export interface IndicatorOutput {
  key: string;
  label?: string;
}

export interface IndicatorSpec {
  id: string;
  label: string;
  pane: "overlay" | "separate";
  inputs?: string[];
  parameters?: IndicatorParameter[];
  outputs?: Array<string | IndicatorOutput>;
  warmup?: number | null;
  description?: string | null;
}

export interface IndicatorInstance {
  instance_id: string;
  type: string;
  params: Record<string, number | boolean | string>;
  color?: string;
  visible?: boolean;
}

export interface IndicatorPreviewRequest {
  instrument_id: string;
  bar_type: string;
  from?: string;
  to?: string;
  indicators: Array<Pick<IndicatorInstance, "instance_id" | "type" | "params">>;
}

export type IndicatorSeries = Record<string, Record<string, Array<number | null>>>;

export interface IndicatorPreviewResponse {
  series: IndicatorSeries;
  warmup?: Record<string, number | null>;
  data_version?: string | null;
  bars?: number | null;
  ts_event?: string[];
  timestamps?: string[];
}

export interface ExecutionSection {
  profile: ExecutionProfile;
  fill_timing?: "next_bar_open" | string;
  fee_bps?: number;
  slippage_bps?: number;
  min_abs_slippage?: number;
  book_staleness_s?: number;
  max_depth_levels?: number;
  fallback?: "conservative";
}

export interface CostEstimate {
  fee_bps?: number | null;
  slippage_bps?: number | null;
  min_abs_slippage?: number | null;
  per_side_bps?: number | null;
  per_side_price_points?: number | null;
  note?: string | null;
}

export interface EffectiveExecution {
  requested: ExecutionProfile;
  effective: ExecutionProfile;
  fallback_reason?: string | null;
  cost_source?: "none" | "fixed" | "dynamic" | string | null;
  fee_bps?: number | null;
  slippage_bps?: number | null;
  min_abs_slippage?: number | null;
  book_coverage?: CoverageWindow | null;
  cost_estimate?: CostEstimate | null;
}

export interface PreflightRequest {
  instrument_id: string;
  bar_type?: string;
  from?: string;
  to?: string;
  execution: ExecutionSection;
  risk?: RiskSection;
}

export interface PreflightResponse extends EffectiveExecution {
  warnings?: string[];
}

export type Comparator =
  | "gt"
  | "gte"
  | "lt"
  | "lte"
  | "crosses_above"
  | "crosses_below";

export type Operand = string | number | boolean | null;

export type RuleNode =
  | { all: RuleNode[] }
  | { any: RuleNode[] }
  | { not: RuleNode }
  | { [comparator in Comparator]?: [Operand, Operand] };

export interface Sizing {
  mode: "fixed_stake" | "risk_pct";
  value: number;
  bankroll_usd?: number;
}

export interface RiskSection {
  sizing: Sizing;
  max_position?: number;
  max_trades?: number;
}

export interface EvaluationSplit {
  train_end: string;
  test_start: string;
  embargo_days?: number;
}

export interface EvaluationSection {
  date_range: { from?: string; to?: string };
  split?: EvaluationSplit;
}

export interface StrategySection {
  instrument_id: string;
  bar_type: string;
  /** Quick mode: the selected built-in, with its primary parameters. */
  builtin_id?: string;
  builtin_params?: Record<string, number | boolean | string>;
  indicators?: IndicatorInstance[];
  direction?: "long" | "short" | "both";
  long_entry?: RuleNode | null;
  long_exit?: RuleNode | null;
  short_entry?: RuleNode | null;
  short_exit?: RuleNode | null;
  settlement?: "hold_to_expiry" | "exit_on_signal" | string;
}

export interface BacktestConfig {
  strategy: StrategySection;
  execution: ExecutionSection;
  risk: RiskSection;
  evaluation: EvaluationSection;
  acknowledged_warnings?: string[];
}

export interface BuiltinStrategy {
  id: string;
  label: string;
  description?: string | null;
  provenance?: string | null;
  direction?: "long" | "short" | "both" | null;
  parameters?: IndicatorParameter[];
  indicators?: IndicatorInstance[];
  defaults?: Record<string, number | boolean | string>;
  settlement?: string;
}

export interface SavedStrategy {
  id: string;
  label: string;
  strategy: StrategySection;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface StrategiesResponse {
  builtins: BuiltinStrategy[];
  saved: SavedStrategy[];
}

export interface MetricSet {
  gross_pnl_usd?: number | null;
  net_pnl_usd?: number | null;
  gross_return_pct?: number | null;
  net_return_pct?: number | null;
  return_pct?: number | null;
  sharpe?: number | null;
  gross_sharpe?: number | null;
  max_drawdown_pct?: number | null;
  trade_count?: number | null;
  win_rate?: number | null;
  avg_win_usd?: number | null;
  avg_loss_usd?: number | null;
  profit_factor?: number | null;
  expectancy_usd?: number | null;
  avg_holding_period_s?: number | null;
  exposure_pct?: number | null;
  fees_usd?: number | null;
  slippage_usd?: number | null;
  avg_entry_probability?: number | null;
  avg_exit_probability?: number | null;
  avg_probability_move_pts?: number | null;
  return_per_capital_deployed_pct?: number | null;
  return_per_notional_pct?: number | null;
  avg_contracts?: number | null;
  settlement_pnl_usd?: number | null;
  trading_pnl_usd?: number | null;
  held_to_settlement_count?: number | null;
  early_exit_count?: number | null;
  /* Aliases used by the backend summary artifact. */
  avg_entry_prob?: number | null;
  avg_exit_prob?: number | null;
  avg_yes_points_captured?: number | null;
  avg_holding_minutes?: number | null;
  cost_bps_of_notional?: number | null;
  starting_cash_usd?: number | null;
  final_equity_usd?: number | null;
  signal_count?: number | null;
  fill_count?: number | null;
  reject_count?: number | null;
}

export interface Metrics extends MetricSet {
  gross?: MetricSet;
  net?: MetricSet;
}

export interface Diagnostics {
  signals_generated?: number | null;
  orders_submitted?: number | null;
  fills?: number | null;
  rejects?: number | null;
  trades_completed?: number | null;
  held_to_settlement?: number | null;
  early_exits?: number | null;
  avg_time_between_signals_s?: number | null;
  avg_holding_time_s?: number | null;
  avg_holding_minutes?: number | null;
  avg_minutes_between_signals?: number | null;
  equity_points?: number | null;
  time_in_market_pct?: number | null;
  avg_entry_price?: number | null;
  avg_exit_price?: number | null;
  leaf_truth_counts?: Record<string, number> | null;
  warnings?: Array<string | { code: string; message: string }>;
}

export interface CostBreakdown {
  fees_usd?: number | null;
  spread_usd?: number | null;
  impact_usd?: number | null;
  slippage_usd?: number | null;
  total_cost_usd?: number | null;
  total_cost_bps?: number | null;
  fee_bps?: number | null;
  slippage_bps?: number | null;
  fallback_fill_count?: number | null;
  fallback_rate?: number | null;
  requested_profile?: ExecutionProfile | null;
  effective_profile?: ExecutionProfile | null;
  fallback_reason?: string | null;
}

export interface PhaseMetric {
  phase: LifecyclePhase;
  trades?: number | null;
  pnl_usd?: number | null;
  return_pct?: number | null;
  sharpe?: number | null;
}

export interface HoldoutState {
  revealed: boolean;
  reveal_count: number;
  split?: EvaluationSplit | null;
  revealed_at?: string | null;
  warning?: string | null;
}

export interface Trade {
  trade_id: string;
  instrument_id?: string;
  side: "BUY" | "SELL";
  close_side?: "BUY" | "SELL" | null;
  order_side?: string | null;
  token: "YES" | "NO";
  intent?: "open_long" | "close_long" | "open_short" | "close_short" | string | null;
  entry_ts: string;
  exit_ts?: string | null;
  /** Null when the artifact omits it; never substituted with a price. */
  entry_price: number | null;
  exit_price?: number | null;
  contracts?: number | null;
  quantity?: number | null;
  entry_notional_usd?: number | null;
  gross_pnl_usd?: number | null;
  net_pnl_usd?: number | null;
  fees_usd?: number | null;
  slippage_usd?: number | null;
  execution_source?: string | null;
  fallback_reason?: string | null;
  held_to_settlement?: boolean | null;
  exit_reason?: string | null;
  holding_minutes?: number | null;
  phase?: LifecyclePhase | null;
  entry_phase?: string | null;
  exit_phase?: string | null;
  entry_context?: Record<string, unknown> | null;
}

export interface EquityPoint {
  ts: string;
  equity_usd: number;
  drawdown_pct?: number | null;
}

export interface SignalPoint {
  ts: string;
  kind: "signal" | "order" | "fill" | "exit" | "settlement" | string;
  side?: "BUY" | "SELL" | string | null;
  token?: string | null;
  price?: number | null;
  reason?: string | null;
}

export interface PositionWindow {
  entry_ts: string;
  exit_ts?: string | null;
  token?: string | null;
  side?: string | null;
}

export interface LifecycleMeta {
  match_key?: string | null;
  actual_kickoff_utc?: string | null;
  actual_end_utc?: string | null;
  settlement_ts?: string | null;
  source?: string | null;
  precision?: string | null;
  phase?: LifecyclePhase | null;
}

export interface RunSummary {
  run_id: string;
  status: RunStatus;
  run_hash?: string | null;
  strategy_hash?: string | null;
  data_version?: string | null;
  lifecycle_version?: string | null;
  created_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  updated_at?: string | null;
  progress?: number | null;
  error?: ApiErrorBody | string | null;
  requested_profile?: ExecutionProfile | null;
  effective_profile?: ExecutionProfile | null;
  fallback_reason?: string | null;
  fallback_rate?: number | null;
  execution?: EffectiveExecution | null;
  config?: BacktestConfig | null;
  metrics?: Metrics | null;
  summary?: Metrics | null;
  manifest?: Record<string, unknown> | null;
  diagnostics?: Diagnostics | null;
  costs?: CostBreakdown | null;
  phases?: PhaseMetric[] | null;
  phase_breakdown?: Record<string, MetricSet> | null;
  holdout?: HoldoutState | null;
  artifact_hashes?: Record<string, string> | null;
  artifacts?: Array<{ name: string; sha256?: string | null }> | null;
  warnings?: Array<string | { code: string; message: string }> | null;
  label?: string | null;
}

export interface RunDetail extends RunSummary {
  equity?: EquityPoint[] | null;
  equity_index?: Array<{ ts: string; equity_index: number | null }> | null;
  drawdown?: Array<{ ts: string; drawdown_pct: number }> | null;
  trades?: Trade[] | null;
  signals?: SignalPoint[] | null;
  open_positions?: PositionWindow[] | null;
  lifecycle?: LifecycleMeta | null;
  phase?: LifecyclePhase | null;
  settlement_value?: 0 | 1 | null;
}

export interface RunListResponse {
  runs: RunSummary[];
  total?: number | null;
}

export interface CreateBacktestResponse {
  run_id: string;
  status: RunStatus;
  run_hash?: string | null;
  strategy_hash?: string | null;
}

export interface CompareResponse {
  runs: RunDetail[];
}

export interface Preset {
  id: string;
  name: string;
  config: BacktestConfig;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface PresetListResponse {
  presets: Preset[];
}

export interface Experiment {
  id: string;
  name?: string | null;
  status: RunStatus;
  completed?: number | null;
  total?: number | null;
  created_at?: string | null;
  config?: Record<string, unknown> | null;
}

export interface ExperimentListResponse {
  experiments: Experiment[];
}
