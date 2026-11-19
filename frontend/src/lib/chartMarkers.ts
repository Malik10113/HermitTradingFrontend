import type {
  Bar,
  LifecycleEvent,
  LifecycleMeta,
  LifecyclePhase,
  PositionWindow,
  SignalPoint,
  Trade,
} from "../api/types";

/** Okabe–Ito palette; candles remain distinguishable in both themes. */
export const CHART_COLORS = {
  up: "#009E73",
  down: "#D55E00",
  neutral: "#E69F00",
  signal: "#56B4E9",
  settlement: "#CC79A7",
  goal: "#0072B2",
  halftime: "#56B4E9",
  positionLong: "rgba(0,158,115,0.10)",
  positionShort: "rgba(213,94,0,0.10)",
  preMatch: "rgba(86,180,233,0.10)",
  inPlay: "rgba(230,159,0,0.13)",
  postMatch: "rgba(204,121,167,0.10)",
  settled: "rgba(154,167,180,0.08)",
} as const;

export interface CandlePoint {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
}

export interface VolumePoint {
  time: number;
  value: number;
  color: string;
}

export interface SeriesPoint {
  time: number;
  value: number;
}

export interface ChartMarker {
  time: number;
  position: "aboveBar" | "belowBar" | "inBar";
  color: string;
  shape: "arrowUp" | "arrowDown" | "circle" | "square";
  text: string;
  id: string;
  kind: "entry" | "exit" | "settlement" | "signal" | "fill";
  tradeId?: string;
}

export interface PhaseWindow {
  phase: LifecyclePhase;
  from: number;
  to: number;
  color: string;
}

export interface PositionBand {
  from: number;
  to: number;
  color: string;
  token: string | null;
}

function toSeconds(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? null : Math.floor(ms / 1000);
}

function validBar(bar: Bar): boolean {
  return (
    typeof bar?.ts_event === "string" &&
    [bar.open, bar.high, bar.low, bar.close].every(
      (value) => typeof value === "number" && Number.isFinite(value),
    ) &&
    Number.isFinite(toSeconds(bar.ts_event))
  );
}

/** Sort ascending by time and drop duplicate timestamps (the last one wins). */
export function barsToCandleData(bars: Bar[]): {
  data: CandlePoint[];
  invalidCount: number;
  duplicateCount: number;
} {
  const byTime = new Map<number, CandlePoint>();
  let invalidCount = 0;
  for (const bar of bars) {
    if (!validBar(bar)) {
      invalidCount += 1;
      continue;
    }
    const time = toSeconds(bar.ts_event) as number;
    byTime.set(time, {
      time,
      open: bar.open,
      high: bar.high,
      low: bar.low,
      close: bar.close,
    });
  }
  const data = Array.from(byTime.values()).sort((a, b) => a.time - b.time);
  return { data, invalidCount, duplicateCount: bars.length - invalidCount - data.length };
}

export function barsToVolumeData(bars: Bar[]): VolumePoint[] {
  return bars
    .filter((bar) => validBar(bar) && typeof bar.volume_usd === "number")
    .map((bar) => ({
      time: toSeconds(bar.ts_event) as number,
      value: bar.volume_usd as number,
      color: bar.close >= bar.open ? "rgba(0,158,115,0.45)" : "rgba(213,94,0,0.45)",
    }))
    .sort((a, b) => a.time - b.time);
}

/**
 * Align a server-computed indicator series to bar timestamps. The frontend
 * never computes indicator values; a length mismatch is surfaced, not patched.
 */
export function alignIndicatorSeries(
  bars: Bar[],
  values: Array<number | null> | undefined,
  timestamps?: string[] | null,
): { points: SeriesPoint[]; mismatch: string | null } {
  if (!values || values.length === 0) return { points: [], mismatch: null };
  if (timestamps && timestamps.length > 0) {
    if (timestamps.length !== values.length) {
      return {
        points: [],
        mismatch: `server series has ${values.length} values for ${timestamps.length} timestamps`,
      };
    }
    const byTime = new Map<number, number>();
    for (let index = 0; index < values.length; index += 1) {
      const value = values[index];
      const time = toSeconds(timestamps[index]);
      if (value === null || !Number.isFinite(value) || time === null) continue;
      byTime.set(time, value);
    }
    return {
      points: Array.from(byTime.entries())
        .map(([time, value]) => ({ time, value }))
        .sort((a, b) => a.time - b.time),
      mismatch: null,
    };
  }
  if (values.length !== bars.length) {
    return {
      points: [],
      mismatch: `server series has ${values.length} values for ${bars.length} bars`,
    };
  }
  const points: SeriesPoint[] = [];
  for (let index = 0; index < bars.length; index += 1) {
    const value = values[index];
    if (value === null || !Number.isFinite(value)) continue;
    const time = toSeconds(bars[index].ts_event);
    if (time === null) continue;
    points.push({ time, value });
  }
  points.sort((a, b) => a.time - b.time);
  return { points, mismatch: null };
}

export function lifecyclePhaseAvailable(lifecycle: LifecycleMeta | null | undefined): boolean {
  return !!(lifecycle?.actual_kickoff_utc && lifecycle?.actual_end_utc);
}

/** Phase at a timestamp, from observed lifecycle timestamps only. */
export function phaseAt(
  iso: string,
  lifecycle: LifecycleMeta | null | undefined,
): LifecyclePhase {
  if (!lifecyclePhaseAvailable(lifecycle)) return "unknown";
  const ts = toSeconds(iso);
  const kickoff = toSeconds(lifecycle?.actual_kickoff_utc);
  const end = toSeconds(lifecycle?.actual_end_utc);
  const settlement = toSeconds(lifecycle?.settlement_ts);
  if (ts === null || kickoff === null || end === null) return "unknown";
  if (ts < kickoff) return "pre_match";
  if (ts < end) return "in_play";
  if (settlement !== null && ts >= settlement) return "settled";
  return "post_match";
}

/**
 * Phase shading windows. No lifecycle row means no shading; the caller shows
 * "phase unknown" instead of inferring a phase.
 */
export function phaseWindows(
  bars: Bar[],
  lifecycle: LifecycleMeta | null | undefined,
): PhaseWindow[] {
  if (!lifecyclePhaseAvailable(lifecycle) || bars.length === 0) return [];
  const candle = barsToCandleData(bars).data;
  if (candle.length === 0) return [];
  const first = candle[0].time;
  const last = candle[candle.length - 1].time;
  const kickoff = toSeconds(lifecycle?.actual_kickoff_utc);
  const end = toSeconds(lifecycle?.actual_end_utc);
  const settlement = toSeconds(lifecycle?.settlement_ts);
  if (kickoff === null || end === null) return [];

  const windows: PhaseWindow[] = [];
  if (kickoff > first) {
    windows.push({
      phase: "pre_match",
      from: first,
      to: Math.min(kickoff, last),
      color: CHART_COLORS.preMatch,
    });
  }
  if (end > Math.max(kickoff, first)) {
    windows.push({
      phase: "in_play",
      from: Math.max(kickoff, first),
      to: Math.min(end, last),
      color: CHART_COLORS.inPlay,
    });
  }
  const postEnd = settlement !== null ? Math.min(settlement, last) : last;
  if (postEnd > Math.max(end, first)) {
    windows.push({
      phase: "post_match",
      from: Math.max(end, first),
      to: postEnd,
      color: CHART_COLORS.postMatch,
    });
  }
  if (settlement !== null && last >= settlement) {
    windows.push({
      phase: "settled",
      from: Math.max(settlement, first),
      to: last,
      color: CHART_COLORS.settled,
    });
  }
  return windows.filter((window) => window.to > window.from);
}

export function positionBands(trades: Trade[]): PositionBand[] {
  const bands: PositionBand[] = [];
  for (const trade of trades) {
    const from = toSeconds(trade.entry_ts);
    if (from === null) continue;
    const to = toSeconds(trade.exit_ts);
    const colour = trade.token === "NO" ? CHART_COLORS.positionShort : CHART_COLORS.positionLong;
    bands.push({
      from,
      to: to ?? from,
      color: colour,
      token: trade.token ?? null,
    });
  }
  return bands;
}

function tradeEntryMarker(trade: Trade): ChartMarker | null {
  const time = toSeconds(trade.entry_ts);
  if (time === null) return null;
  const buy = trade.side === "BUY";
  return {
    time,
    position: buy ? "belowBar" : "aboveBar",
    color: buy ? CHART_COLORS.up : CHART_COLORS.down,
    shape: buy ? "arrowUp" : "arrowDown",
    text: `${trade.side} ${trade.token}`,
    id: `${trade.trade_id}:entry`,
    kind: "entry",
    tradeId: trade.trade_id,
  };
}

function tradeExitMarker(trade: Trade): ChartMarker | null {
  if (!trade.exit_ts) return null;
  const time = toSeconds(trade.exit_ts);
  if (time === null) return null;
  if (trade.held_to_settlement) {
    return {
      time,
      position: "aboveBar",
      color: CHART_COLORS.settlement,
      shape: "circle",
      text: `SETTLE ${trade.token}`,
      id: `${trade.trade_id}:settlement`,
      kind: "settlement",
      tradeId: trade.trade_id,
    };
  }
  // A missing close side is not assumed to be SELL: name the event instead.
  const closeSide = trade.close_side;
  return {
    time,
    position: "aboveBar",
    color: CHART_COLORS.down,
    shape: "arrowDown",
    text: closeSide ? `${closeSide} ${trade.token}` : `EXIT ${trade.token}`,
    id: `${trade.trade_id}:exit`,
    kind: "exit",
    tradeId: trade.trade_id,
  };
}

/** Entry/exit/settlement markers for the candle series. */
export function tradeMarkers(trades: Trade[]): ChartMarker[] {
  const markers: ChartMarker[] = [];
  for (const trade of trades) {
    const entry = tradeEntryMarker(trade);
    const exit = tradeExitMarker(trade);
    if (entry) markers.push(entry);
    if (exit) markers.push(exit);
  }
  return markers.sort((a, b) => a.time - b.time || a.id.localeCompare(b.id));
}

export function signalMarkers(signals: SignalPoint[]): ChartMarker[] {
  const markers: ChartMarker[] = [];
  signals.forEach((signal, index) => {
    const time = toSeconds(signal.ts);
    if (time === null) return;
    const kind = signal.kind === "settlement" ? "settlement" : signal.kind === "fill" ? "fill" : "signal";
    markers.push({
      time,
      position: signal.side === "SELL" ? "aboveBar" : "belowBar",
      color:
        kind === "settlement"
          ? CHART_COLORS.settlement
          : kind === "fill"
            ? CHART_COLORS.neutral
            : CHART_COLORS.signal,
      shape: "square",
      text: signal.reason ? `${signal.kind} ${signal.side ?? ""}`.trim() : kind.toUpperCase(),
      id: `signal:${index}:${time}`,
      kind: kind as ChartMarker["kind"],
    });
  });
  return markers.sort((a, b) => a.time - b.time);
}

export function mergeMarkers(...groups: ChartMarker[][]): ChartMarker[] {
  const seen = new Set<string>();
  const merged: ChartMarker[] = [];
  for (const marker of groups.flat()) {
    if (seen.has(marker.id)) continue;
    seen.add(marker.id);
    merged.push(marker);
  }
  return merged.sort((a, b) => a.time - b.time || a.id.localeCompare(b.id));
}

/**
 * Kickoff and end markers, only when both wallclocks are observed. A missing
 * end time is not replaced by the kickoff (that would invent an end).
 */
export function lifecycleMarkers(
  lifecycle: LifecycleMeta | null | undefined,
): ChartMarker[] {
  if (!lifecyclePhaseAvailable(lifecycle)) return [];
  const kickoff = toSeconds(lifecycle?.actual_kickoff_utc);
  const end = toSeconds(lifecycle?.actual_end_utc);
  if (kickoff === null || end === null) return [];
  return [
    {
      time: kickoff,
      position: "aboveBar",
      color: CHART_COLORS.neutral,
      shape: "arrowDown",
      text: "KICKOFF",
      id: "lifecycle:kickoff",
      kind: "signal",
    },
    {
      time: end,
      position: "aboveBar",
      color: CHART_COLORS.neutral,
      shape: "circle",
      text: "END",
      id: "lifecycle:end",
      kind: "signal",
    },
  ];
}

/**
 * Halftime and goal markers. Halftime is always shown; goals only when the
 * caller asked for them. Events with no parseable time are skipped, never
 * placed at a guessed time.
 */
export function lifecycleEventMarkers(
  events: LifecycleEvent[] | null | undefined,
  showGoals = true,
): ChartMarker[] {
  const markers: ChartMarker[] = [];
  (events ?? []).forEach((event, index) => {
    if (event.type !== "halftime" && event.type !== "goal") return;
    if (event.type === "goal" && !showGoals) return;
    const time = toSeconds(event.ts);
    if (time === null) return;
    const halftime = event.type === "halftime";
    markers.push({
      time,
      position: halftime ? "aboveBar" : "belowBar",
      color: halftime ? CHART_COLORS.halftime : CHART_COLORS.goal,
      shape: halftime ? "circle" : "square",
      text: halftime ? "HALF TIME" : "GOAL",
      id: `lifecycle:${event.type}:${index}:${time}`,
      kind: "signal",
    });
  });
  return markers.sort((a, b) => a.time - b.time || a.id.localeCompare(b.id));
}

/**
 * Every marker the chart shows for a run: entry/exit/settlement from the trade
 * artifact, signal/fill evidence from the signals artifact, then observed
 * lifecycle events. Signatures are from the artifacts, never inferred here.
 */
export function runChartMarkers({
  trades,
  signals,
  lifecycle,
  events,
  showGoals = true,
}: {
  trades: Trade[];
  signals: SignalPoint[];
  lifecycle: LifecycleMeta | null | undefined;
  events?: LifecycleEvent[] | null;
  showGoals?: boolean;
}): ChartMarker[] {
  return mergeMarkers(
    tradeMarkers(trades),
    signalMarkers(signals),
    lifecycleMarkers(lifecycle),
    lifecycleEventMarkers(events ?? lifecycle?.events, showGoals),
  );
}

export interface EquitySeries {
  label: string;
  color: string;
  points: Array<{ time: number; value: number }>;
}

/**
 * Normalise each equity curve to 100 at the common start so 2–3 runs can be
 * overlaid. Curves shorter than two points are returned empty.
 */
export function normaliseEquityCurves(
  runs: Array<{ label: string; color?: string; equity: Array<{ ts: string; equity_usd: number }> }>,
): EquitySeries[] {
  const prepared = runs.map((run, index) => {
    const points = run.equity
      .map((point) => ({ time: toSeconds(point.ts), value: point.equity_usd }))
      .filter(
        (point): point is { time: number; value: number } =>
          point.time !== null && Number.isFinite(point.value),
      )
      .sort((a, b) => a.time - b.time);
    return {
      label: run.label,
      color: run.color ?? [CHART_COLORS.signal, CHART_COLORS.neutral, CHART_COLORS.settlement][index % 3],
      points,
    };
  });
  const starts = prepared
    .filter((run) => run.points.length >= 2)
    .map((run) => run.points[0].time);
  if (starts.length === 0) return [];
  const commonStart = Math.max(...starts);
  return prepared
    .filter((run) => run.points.length >= 2)
    .map((run) => {
      const window = run.points.filter((point) => point.time >= commonStart);
      const base = window[0]?.value;
      if (window.length < 2 || !base) {
        return { label: run.label, color: run.color, points: [] };
      }
      return {
        label: run.label,
        color: run.color,
        points: window.map((point) => ({
          time: point.time,
          value: (point.value / base) * 100,
        })),
      };
    });
}

export function openPositionBands(windows: PositionWindow[] | null | undefined): PositionBand[] {
  if (!windows) return [];
  const bands: PositionBand[] = [];
  for (const window of windows) {
    const from = toSeconds(window.entry_ts);
    if (from === null) continue;
    const to = toSeconds(window.exit_ts) ?? from;
    bands.push({
      from,
      to,
      color:
        window.token === "NO" || window.side === "short"
          ? CHART_COLORS.positionShort
          : CHART_COLORS.positionLong,
      token: window.token ?? null,
    });
  }
  return bands;
}
