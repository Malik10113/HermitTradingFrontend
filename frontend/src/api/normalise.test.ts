import { describe, expect, it } from "vitest";
import {
  instrumentLabel,
  normaliseCompare,
  normaliseInstrumentList,
  normaliseMetrics,
  normalisePositions,
  normalisePresets,
  normaliseQuality,
  normaliseRun,
  normaliseStrategies,
  normaliseTrades,
  tokenOf,
} from "../api/normalise";

describe("wire normalisation", () => {
  it("reads the compact instrument list and derives YES/NO from token_index", () => {
    const list = normaliseInstrumentList({
      items: [
        {
          instrument_id: "epl-x-y-2024-10-19-x-YES.POLYMARKET",
          question: "Will X win?",
          token_index: 0,
          selection: "home",
          first_ts: "2024-10-01T12:00:00Z",
          last_ts: "2024-10-02T12:00:00Z",
          price_precision: 3,
        },
        {
          instrument_id: "epl-x-y-2024-10-19-x-NO.POLYMARKET",
          question: "Will X win?",
          token_index: 1,
          selection: "home",
          settlement: { yes_settlement: 1, no_settlement: 0 },
        },
      ],
      total: 2,
    });
    expect(list.instruments).toHaveLength(2);
    expect(list.total).toBe(2);
    expect(tokenOf(list.instruments[0])).toBe("YES");
    expect(tokenOf(list.instruments[1])).toBe("NO");
    expect(list.instruments[0].coverage).toEqual({
      from: "2024-10-01T12:00:00Z",
      to: "2024-10-02T12:00:00Z",
    });
    expect(list.instruments[0].price_tick).toBe(0.001);
    expect(list.instruments[1].settlement_value).toBe(0);
    expect(list.instruments[1].settlement_known).toBe(true);
    expect(instrumentLabel(list.instruments[0])).toBe("Will X win? · YES");
  });

  it("normalises quality aliases and warning objects", () => {
    const quality = normaliseQuality({
      instrument_id: "x",
      available_from_utc: "2024-10-01T00:00:00Z",
      available_to_utc: "2024-10-02T00:00:00Z",
      active_minutes_pct: 30,
      longest_gap_minutes: 8,
      median_trade_size_usd: 5.01,
      warnings: [{ code: "very_few_trades", message: "Only 5 trades." }],
      bar_count: 3,
    });
    expect(quality.available_from).toBe("2024-10-01T00:00:00Z");
    expect(quality.active_minute_pct).toBe(30);
    expect(quality.longest_gap_s).toBe(480);
    expect(quality.median_trade_usd).toBe(5.01);
    expect(quality.warnings).toEqual(["Only 5 trades."]);
    expect(quality.gaps).toBeNull();
  });

  it("maps summary metrics aliases without inventing missing values", () => {
    const metrics = normaliseMetrics({
      gross_pnl_usd: 10,
      net_pnl_usd: 8,
      avg_entry_prob: 0.4,
      avg_exit_prob: 0.5,
      avg_yes_points_captured: 0.1,
      avg_holding_minutes: 60,
      trade_count: 2,
      profit_factor: null,
    });
    expect(metrics?.avg_entry_probability).toBe(0.4);
    expect(metrics?.avg_exit_probability).toBe(0.5);
    expect(metrics?.avg_probability_move_pts).toBe(0.1);
    expect(metrics?.avg_holding_period_s).toBe(3600);
    expect(metrics?.profit_factor).toBeNull();
    expect(metrics?.win_rate).toBeNull();
  });

  it("normalises a run summary plus detail artifacts", () => {
    const run = normaliseRun({
      run_id: "run_1",
      status: "complete",
      updated_at: "2024-10-20T09:00:00Z",
      requested_profile: "book",
      effective_profile: "conservative",
      fallback_reason: "no_book_coverage",
      summary: { net_pnl_usd: 8, trade_count: 1, max_drawdown_pct: -0.6 },
      manifest: {
        lifecycle_version: "sha256:life",
        execution: {
          requested: "book",
          effective: "conservative",
          fallback_reason: "no_book_coverage",
          book_coverage: {
            has_coverage: true,
            window: { first_month: "2024-01", last_month: "2024-10" },
          },
        },
        phase_breakdown: {
          pre_match: { trade_count: 1, net_pnl_usd: 8, return_pct: 0.8 },
        },
      },
      equity: [
        { ts: "2024-10-19T10:00:00Z", equity_usd: 1000, drawdown_pct: 0 },
        { ts: "2024-10-19T11:00:00Z", equity_usd: 1010, drawdown_pct: 0 },
      ],
      trades: [
        {
          entry_ts: "2024-10-19T10:05:00Z",
          exit_ts: "2024-10-19T10:55:00Z",
          token: "YES",
          side: "long",
          quantity: 10,
          entry_price: 0.4,
          exit_price: 0.5,
          net_pnl_usd: 1,
          exit_reason: "settlement",
          entry_phase: "pre_match",
        },
      ],
    });
    expect(run.completed_at).toBe("2024-10-20T09:00:00Z");
    expect(run.execution?.effective).toBe("conservative");
    expect(run.execution?.book_coverage?.first_month).toBe("2024-01");
    expect(run.lifecycle_version).toBe("sha256:life");
    expect(run.metrics?.net_pnl_usd).toBe(8);
    expect(run.equity).toHaveLength(2);
    expect(run.trades?.[0]).toMatchObject({
      token: "YES",
      side: "BUY",
      intent: "open_long",
      contracts: 10,
      held_to_settlement: true,
      phase: "pre_match",
    });
    expect(run.phases).toEqual([
      { phase: "pre_match", trades: 1, pnl_usd: 8, return_pct: 0.8, sharpe: null },
    ]);
  });

  it("uses the precomputed equity index from a comparison", () => {
    const compare = normaliseCompare({
      runs: [
        {
          run_id: "a",
          status: "complete",
          equity_index: [
            { ts: "2024-10-19T10:00:00Z", equity_index: 100 },
            { ts: "2024-10-19T11:00:00Z", equity_index: 102 },
          ],
        },
      ],
    });
    expect(compare.runs[0].equity?.map((point) => point.equity_usd)).toEqual([100, 102]);
  });

  it("maps the signals artifact reference_price alias and keeps absent prices null", () => {
    const run = normaliseRun({
      run_id: "run_sig",
      status: "complete",
      signals: [
        {
          ts: "2024-10-19T10:00:00Z",
          ts_ns: Date.parse("2024-10-19T10:00:00Z") * 1e6,
          action: "long_entry",
          kind: "signal",
          side: "BUY",
          token: "YES",
          reference_price: 0.42,
          reason: "ema_cross",
        },
        { ts_ns: Date.parse("2024-10-19T10:05:00Z") * 1e6, action: "fill" },
        { action: "no_timestamp" },
      ],
    });
    expect(run.signals).toHaveLength(2);
    expect(run.signals?.[0].price).toBe(0.42);
    expect(run.signals?.[0].reason).toBe("ema_cross");
    expect(run.signals?.[1].price).toBeNull();
    expect(run.signals?.[1].kind).toBe("fill");
    expect(run.signals?.[1].ts).toBe("2024-10-19T10:05:00.000Z");
  });

  it("maps trade artifact rows into the trade contract", () => {
    const trades = normaliseTrades([
      {
        entry_ts: "2024-10-19T10:00:00Z",
        token: "NO",
        side: "short",
        quantity: 5,
        entry_price: 0.3,
        exit_price: 0.2,
        exit_reason: "rule_exit",
      },
    ]);
    expect(trades[0]).toMatchObject({
      trade_id: "T-0001",
      token: "NO",
      intent: "open_short",
      side: "BUY",
      direction: "short",
      contracts: 5,
      held_to_settlement: false,
    });
  });

  it("preserves direction when the API already supplies an order side", () => {
    const trades = normaliseTrades([
      {
        entry_ts: "2024-10-19T10:00:00Z",
        token: "YES",
        side: "BUY",
        direction: "long",
        intent: "open_long",
        quantity: 5,
      },
    ]);
    expect(trades[0].side).toBe("BUY");
    expect(trades[0].direction).toBe("long");
    expect(trades[0].intent).toBe("open_long");
  });

  it("maps artifact position windows without inventing an exit", () => {
    const windows = normalisePositions([
      {
        position_id: "P-0001",
        entry_ts: "2024-10-19T10:00:00Z",
        exit_ts: "2024-10-19T10:05:00Z",
        token: "YES",
        side: "long",
        intent: "open_long",
        quantity: 5,
        entry_price: 0.4,
        exit_price: 0.5,
        settlement: false,
        status: "closed",
        entry_execution_source: "conservative_fallback",
        exit_execution_source: "book",
      },
      {
        position_id: "O-0001",
        entry_ts: "2024-10-19T10:10:00Z",
        token: "NO",
        side: "short",
        quantity: 2,
        status: "open",
      },
    ]);
    expect(windows).not.toBeNull();
    expect(windows?.[0]).toMatchObject({
      position_id: "P-0001",
      side: "long",
      settlement: false,
      entry_execution_source: "conservative_fallback",
      exit_execution_source: "book",
    });
    expect(windows?.[1].exit_ts).toBeNull();
    expect(windows?.[1].status).toBe("open");
    expect(normalisePositions(null)).toBeNull();
    expect(normalisePositions([])).toEqual([]);
  });

  it("keeps absent trade prices and close side absent instead of inventing them", () => {
    const trades = normaliseTrades([
      {
        entry_ts: "2024-10-19T10:00:00Z",
        token: "YES",
        side: "long",
        quantity: 5,
      },
    ]);
    expect(trades[0].entry_price).toBeNull();
    expect(trades[0].exit_price).toBeNull();
    expect(trades[0].close_side).toBeNull();
  });

  it("normalises builtin parameter keys so the rail can edit them", () => {
    const strategies = normaliseStrategies({
      builtins: [
        {
          id: "ema",
          label: "EMA cross",
          params: [{ key: "fast_period", label: "Fast", type: "int", default: 12 }],
          defaults: { fast_period: 12 },
        },
      ],
      saved: [{ id: "s1", name: "Saved", config: { instrument_id: "x", bar_type: "1m" } }],
    });
    expect(strategies.builtins[0].parameters?.[0].name).toBe("fast_period");
    expect(strategies.builtins[0].defaults).toEqual({ fast_period: 12 });
    expect(strategies.saved[0].label).toBe("Saved");
  });

  it("normalises the presets items envelope", () => {
    const presets = normalisePresets({
      items: [
        {
          id: "pre_1",
          name: "Preset",
          config: {
            strategy: { instrument_id: "x", bar_type: "1m" },
            execution: { profile: "conservative" },
            risk: { sizing: { mode: "fixed_stake", value: 100 } },
            evaluation: { date_range: { start: "2024-01-01", end: "2024-02-01" } },
          },
          created_at: "2024-10-20T00:00:00Z",
        },
      ],
    });
    expect(presets.presets).toHaveLength(1);
    expect(presets.presets[0].name).toBe("Preset");
  });
});
