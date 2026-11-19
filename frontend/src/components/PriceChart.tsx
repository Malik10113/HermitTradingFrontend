import { useEffect, useMemo, useRef, useState } from "react";
import {
  ColorType,
  CrosshairMode,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type LogicalRange,
  type MouseEventParams,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import type { Bar, LifecycleMeta, PositionWindow, SignalPoint, Trade } from "../api/types";
import type { PhaseFilter } from "../lib/urlState";
import {
  barsToCandleData,
  barsToVolumeData,
  CHART_COLORS,
  lifecyclePhaseAvailable,
  openPositionBands,
  phaseWindows,
  runChartMarkers,
  type SeriesPoint,
} from "../lib/chartMarkers";
import { INDICATOR_PALETTE } from "../lib/palette";
import { useTheme } from "../state/theme";
import { fmtDuration, fmtProbability, fmtTimestamp, fmtUsd } from "../lib/format";
import { EmptyState } from "./ui";

export interface ChartIndicatorLine {
  key: string;
  label: string;
  points: SeriesPoint[];
}

export interface ChartIndicatorSeries {
  id: string;
  label: string;
  color: string;
  pane: "overlay" | "separate";
  lines: ChartIndicatorLine[];
  warmup?: number | null;
}

interface PriceChartProps {
  bars: Bar[];
  indicators?: ChartIndicatorSeries[];
  lifecycle?: LifecycleMeta | null;
  trades?: Trade[];
  signals?: SignalPoint[];
  /** Artifact-derived position windows; shading is never inferred from trades. */
  positions?: PositionWindow[];
  phaseFilter?: PhaseFilter;
  stale?: boolean;
  height?: number;
  onSelectTrade?: (tradeId: string) => void;
}

interface OverlayBand {
  key: string;
  left: number;
  width: number;
  color: string;
  label: string;
  kind: "phase" | "position";
}

const PHASE_LABELS: Record<string, string> = {
  pre_match: "pre-match",
  in_play: "in-play",
  post_match: "post-match",
  settled: "settled",
};

/**
 * Each output of one indicator gets its own hue so a MACD's value, signal and
 * histogram are distinguishable. The first line keeps the instance colour;
 * later lines take palette entries that differ from it, stable by index.
 */
export function outputLineColor(instanceColor: string, index: number): string {
  if (index === 0) return instanceColor;
  const candidates = INDICATOR_PALETTE.filter(
    (color) => color.toLowerCase() !== instanceColor.toLowerCase(),
  );
  if (candidates.length === 0) return instanceColor;
  return candidates[(index - 1) % candidates.length];
}

/** `MACD · Signal`, or just `MACD` when the output label adds nothing. */
export function outputLineTitle(indicatorLabel: string, lineLabel: string): string {
  if (!lineLabel || lineLabel === indicatorLabel) return indicatorLabel;
  return `${indicatorLabel} · ${lineLabel}`;
}

export function PriceChart({
  bars,
  indicators = [],
  lifecycle = null,
  trades = [],
  signals = [],
  positions = [],
  phaseFilter = "all",
  stale = false,
  height = 460,
  onSelectTrade,
}: PriceChartProps) {
  const { theme } = useTheme();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const overlayRef = useRef<HTMLDivElement | null>(null);
  const separateContainers = useRef(new Map<string, HTMLDivElement>());
  const onSelectTradeRef = useRef(onSelectTrade);
  onSelectTradeRef.current = onSelectTrade;
  const [overlay, setOverlay] = useState<OverlayBand[]>([]);
  const [chartError, setChartError] = useState<string | null>(null);
  // Cursor state lives outside the chart effect: hovering must never rebuild
  // the canvas, only re-render the legend strip.
  const [hovered, setHovered] = useState<Bar | null>(null);

  const candle = useMemo(() => barsToCandleData(bars), [bars]);
  const volume = useMemo(() => barsToVolumeData(bars), [bars]);
  const barByTime = useMemo(() => {
    const byTime = new Map<number, Bar>();
    for (const bar of bars) {
      const ms = Date.parse(bar.ts_event);
      if (Number.isNaN(ms)) continue;
      byTime.set(Math.floor(ms / 1000), bar);
    }
    return byTime;
  }, [bars]);
  const barByTimeRef = useRef(barByTime);
  barByTimeRef.current = barByTime;
  const separateIndicators = indicators.filter(
    (indicator) => indicator.pane === "separate" && indicator.lines.length > 0,
  );

  // Stable signatures avoid rebuilding the canvas when the parent re-renders
  // for an unrelated reason (a keystroke, a rail toggle, a poll tick).
  const dataSignature = useMemo(() => {
    const first = candle.data[0];
    const end = candle.data[candle.data.length - 1];
    const checksum = candle.data.reduce((sum, point) => sum + point.close, 0);
    return `${candle.data.length}|${first?.time ?? 0}|${end?.time ?? 0}|${checksum.toFixed(4)}|${volume.length}`;
  }, [candle.data, volume.length]);

  const indicatorSignature = useMemo(
    () =>
      indicators
        .map(
          (indicator) =>
            `${indicator.id}:${indicator.pane}:${indicator.color}:${indicator.lines
              .map(
                (line) =>
                  `${line.key}:${line.points.length}:${line.points
                    .reduce((sum, point) => sum + point.value, 0)
                    .toFixed(4)}`,
              )
              .join(",")}`,
        )
        .join(";"),
    [indicators],
  );

  const markerSignature = useMemo(
    () =>
      trades
        .map((trade) => `${trade.trade_id}:${trade.entry_ts}:${trade.exit_ts ?? ""}:${trade.token}`)
        .join("|") +
      `::${signals.map((signal) => `${signal.ts}:${signal.kind}`).join("|")}`,
    [trades, signals],
  );

  const lifecycleSignature = useMemo(
    () =>
      lifecycle
        ? `${lifecycle.actual_kickoff_utc ?? ""}|${lifecycle.actual_end_utc ?? ""}|${lifecycle.settlement_ts ?? ""}`
        : "",
    [lifecycle],
  );

  const positionSignature = useMemo(
    () =>
      positions
        .map(
          (window) =>
            `${window.position_id ?? ""}:${window.entry_ts}:${window.exit_ts ?? ""}:${window.token ?? ""}:${window.side ?? ""}`,
        )
        .join("|"),
    [positions],
  );

  const last = candle.data[candle.data.length - 1];
  const lastBar = bars[bars.length - 1];
  const legendCandle = hovered ?? last;
  const legendBar = hovered ?? lastBar;

  useEffect(() => {
    const container = containerRef.current;
    if (!container || candle.data.length === 0) return;
    setChartError(null);

    const dark = theme === "dark";
    let chart: IChartApi;
    try {
      chart = createChart(container, {
        width: container.clientWidth || 600,
        height,
        layout: {
          background: { type: ColorType.Solid, color: dark ? "#0b0f14" : "#ffffff" },
          textColor: dark ? "#9aa7b4" : "#52606d",
          fontSize: 11,
        },
        grid: {
          vertLines: { color: dark ? "#1b232d" : "#e9edf2" },
          horzLines: { color: dark ? "#1b232d" : "#e9edf2" },
        },
        crosshair: { mode: CrosshairMode.Normal },
        rightPriceScale: {
          borderColor: dark ? "#212b36" : "#d6dde5",
          scaleMargins: { top: 0.06, bottom: 0.02 },
        },
        timeScale: {
          borderColor: dark ? "#212b36" : "#d6dde5",
          timeVisible: true,
          secondsVisible: false,
          rightOffset: 4,
        },
        localization: {
          priceFormatter: (price: number) => price.toFixed(3),
        },
        handleScroll: { mouseWheel: true, pressedMouseMove: true },
        handleScale: { axisPressedMouseMove: true, mouseWheel: true, pinch: true },
      });
    } catch (error) {
      setChartError(error instanceof Error ? error.message : "Chart could not be created");
      return;
    }

    chartRef.current = chart;
    const candleSeries = chart.addCandlestickSeries({
      upColor: CHART_COLORS.up,
      downColor: CHART_COLORS.down,
      borderUpColor: CHART_COLORS.up,
      borderDownColor: CHART_COLORS.down,
      wickUpColor: CHART_COLORS.up,
      wickDownColor: CHART_COLORS.down,
      priceLineColor: dark ? "#6f7d8b" : "#77828f",
      priceFormat: { type: "price", precision: 3, minMove: 0.001 },
    });
    candleRef.current = candleSeries;
    candleSeries.setData(
      candle.data.map((point) => ({
        time: point.time as UTCTimestamp,
        open: point.open,
        high: point.high,
        low: point.low,
        close: point.close,
      })),
    );

    if (volume.length > 0) {
      const volumeSeries = chart.addHistogramSeries({
        priceFormat: { type: "volume" },
        priceScaleId: "",
        priceLineVisible: false,
        lastValueVisible: false,
      });
      volumeSeries.priceScale().applyOptions({
        scaleMargins: { top: 0.82, bottom: 0 },
      });
      volumeSeries.setData(
        volume.map((point) => ({
          time: point.time as UTCTimestamp,
          value: point.value,
          color: point.color,
        })),
      );
    }

    for (const indicator of indicators) {
      if (indicator.pane !== "overlay") continue;
      indicator.lines.forEach((line, index) => {
        if (line.points.length === 0) return;
        const lineSeries = chart.addLineSeries({
          color: outputLineColor(indicator.color, index),
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: true,
          title: outputLineTitle(indicator.label, line.label),
          priceFormat: { type: "price", precision: 3, minMove: 0.001 },
        });
        lineSeries.setData(
          line.points.map((point) => ({
            time: point.time as UTCTimestamp,
            value: point.value,
          })),
        );
      });
    }

    // Trades and signals are independent artifacts: merging (not choosing
    // between them) keeps fill evidence visible once a trade list exists.
    const markerList = runChartMarkers({ trades, signals, lifecycle }).filter((marker) =>
      Number.isFinite(marker.time),
    );

    const tickets = markerList.filter(
      (marker) =>
        Number.isFinite(marker.time) &&
        marker.time >= candle.data[0].time &&
        marker.time <= candle.data[candle.data.length - 1].time,
    );
    // Dense runs keep the arrows but drop labels; settlement stays explicit.
    const compactMarkers = tickets.length > 24;
    candleSeries.setMarkers(
      tickets.map((marker) => ({
        time: marker.time as UTCTimestamp,
        position: marker.position,
        color: marker.color,
        shape: marker.shape,
        size: compactMarkers ? 0.6 : 1,
        text:
          compactMarkers &&
          marker.kind !== "settlement" &&
          !marker.id.startsWith("lifecycle:")
            ? ""
            : marker.text,
        id: marker.id,
      })) as SeriesMarker<Time>[],
    );

    chart.timeScale().fitContent();

    // Phase and open-position bands are drawn as a DOM overlay because the
    // charting library has no native vertical-band primitive.
    const updateBands = () => {
      if (!overlayRef.current) return;
      const width = chart.timeScale().width();
      if (width <= 0) {
        setOverlay([]);
        return;
      }
      const visible = chart.timeScale().getVisibleRange();
      const visibleFrom = visible ? Number(visible.from) : -Infinity;
      const visibleTo = visible ? Number(visible.to) : Infinity;
      const bands: OverlayBand[] = [];
      const phaseBands = phaseFilter === "all"
        ? phaseWindows(bars, lifecycle)
        : phaseWindows(bars, lifecycle).filter((window) => window.phase === phaseFilter);
      for (const window of phaseBands) {
        const rawLeft = chart.timeScale().timeToCoordinate(window.from as UTCTimestamp);
        const rawRight = chart.timeScale().timeToCoordinate(window.to as UTCTimestamp);
        const left = rawLeft === null ? (window.from < visibleFrom ? 0 : null) : Number(rawLeft);
        const right = rawRight === null ? (window.to > visibleTo ? width : null) : Number(rawRight);
        if (left === null || right === null || right <= left) continue;
        bands.push({
          key: `phase:${window.phase}:${window.from}`,
          left,
          width: right - left,
          color: window.color,
          label: PHASE_LABELS[window.phase] ?? window.phase,
          kind: "phase",
        });
      }
      const allPositions = openPositionBands(positions);
      for (const [index, band] of allPositions.entries()) {
        const rawLeft = chart.timeScale().timeToCoordinate(band.from as UTCTimestamp);
        const rawRight = chart.timeScale().timeToCoordinate(band.to as UTCTimestamp);
        const left = rawLeft === null ? (band.from < visibleFrom ? 0 : null) : Number(rawLeft);
        const right = rawRight === null ? (band.to > visibleTo ? width : null) : Number(rawRight);
        if (left === null || right === null || right <= left) continue;
        bands.push({
          key: `position:${index}:${band.from}`,
          left,
          width: Math.max(1, right - left),
          color: band.color,
          label: `open position ${band.token ?? ""}`.trim(),
          kind: "position",
        });
      }
      setOverlay(bands);
    };

    chart.timeScale().subscribeVisibleLogicalRangeChange(updateBands);
    const resizeObserver = new ResizeObserver(() => {
      chart.applyOptions({ width: container.clientWidth || 600 });
      updateBands();
    });
    resizeObserver.observe(container);
    window.requestAnimationFrame(updateBands);

    chart.subscribeClick((param) => {
      if (!param.time || !onSelectTradeRef.current) return;
      const seconds = Number(param.time);
      const hit = markerList.find(
        (marker) => marker.tradeId && Math.abs(marker.time - seconds) <= 1,
      );
      if (hit?.tradeId) onSelectTradeRef.current(hit.tradeId);
    });

    // The legend follows the cursor; a hovered time with no stored bar falls
    // back to the last bar rather than inventing a candle. A chart harness may
    // expose a narrower API surface, so the subscription is capability-checked.
    const handleCrosshairMove = (param: MouseEventParams<Time>) => {
      if (param.time === undefined || param.time === null) {
        setHovered(null);
        return;
      }
      const seconds = Number(param.time);
      if (!Number.isFinite(seconds)) {
        setHovered(null);
        return;
      }
      setHovered(barByTimeRef.current.get(Math.floor(seconds)) ?? null);
    };
    const tracksCrosshair = typeof chart.subscribeCrosshairMove === "function";
    if (tracksCrosshair) chart.subscribeCrosshairMove(handleCrosshairMove);

    // Each separate-pane indicator gets its own mini chart with its own price
    // scale and ResizeObserver, time-synced with the main chart both ways.
    const paneCharts: IChartApi[] = [];
    const paneObservers: ResizeObserver[] = [];
    let syncingRange = false;
    const mainRange = chart.timeScale();
    const propagateRange = (source: IChartApi | null, range: LogicalRange | null) => {
      if (!range || syncingRange) return;
      syncingRange = true;
      try {
        if (source !== chart) mainRange.setVisibleLogicalRange(range);
        for (const pane of paneCharts) {
          if (pane !== source) pane.timeScale().setVisibleLogicalRange(range);
        }
      } finally {
        syncingRange = false;
      }
    };

    for (const indicator of separateIndicators) {
      const paneContainer = separateContainers.current.get(indicator.id);
      if (!paneContainer) continue;
      const pane = createChart(paneContainer, {
        width: paneContainer.clientWidth || 600,
        height: 126,
        layout: {
          background: { type: ColorType.Solid, color: dark ? "#0b0f14" : "#ffffff" },
          textColor: dark ? "#9aa7b4" : "#52606d",
          fontSize: 10,
        },
        grid: {
          vertLines: { color: dark ? "#1b232d" : "#e9edf2" },
          horzLines: { color: dark ? "#1b232d" : "#e9edf2" },
        },
        rightPriceScale: { borderColor: dark ? "#212b36" : "#d6dde5" },
        timeScale: { borderColor: dark ? "#212b36" : "#d6dde5", timeVisible: true, visible: false },
        handleScroll: false,
        handleScale: false,
        crosshair: { mode: CrosshairMode.Normal },
      });
      paneCharts.push(pane);
      indicator.lines.forEach((line, index) => {
        if (line.points.length === 0) return;
        const series = pane.addLineSeries({
          color: outputLineColor(indicator.color, index),
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
          title: outputLineTitle(indicator.label, line.label),
        });
        series.setData(
          line.points.map((point) => ({
            time: point.time as UTCTimestamp,
            value: point.value,
          })),
        );
      });
      const paneResize = new ResizeObserver(() => {
        pane.applyOptions({ width: paneContainer.clientWidth || 600 });
      });
      paneResize.observe(paneContainer);
      paneObservers.push(paneResize);
      pane.timeScale().subscribeVisibleLogicalRangeChange((range) =>
        propagateRange(pane, range),
      );
      pane.timeScale().fitContent();
    }
    if (paneCharts.length > 0) {
      mainRange.subscribeVisibleLogicalRangeChange((range) => propagateRange(chart, range));
    }

    return () => {
      resizeObserver.disconnect();
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(updateBands);
      if (tracksCrosshair) chart.unsubscribeCrosshairMove(handleCrosshairMove);
      for (const observer of paneObservers) observer.disconnect();
      for (const pane of paneCharts) pane.remove();
      chart.remove();
      chartRef.current = null;
      candleRef.current = null;
      setOverlay([]);
      setHovered(null);
    };
    // Chart is rebuilt when the data signature changes; the range is re-fitted,
    // which keeps a research view reproducible rather than sticky.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    dataSignature,
    indicatorSignature,
    markerSignature,
    lifecycleSignature,
    positionSignature,
    theme,
    height,
    phaseFilter,
  ]);

  if (candle.data.length === 0) {
    return (
      <EmptyState
        title="No bars to display"
        message="No trades produced a bar in this range. Empty intervals are not filled with synthetic candles."
      />
    );
  }
  if (chartError) {
    return (
      <EmptyState
        title="Chart unavailable"
        message={`The charting library could not start in this browser: ${chartError}`}
      />
    );
  }

  return (
    <div className="relative">
      <div
        className="flex flex-wrap items-baseline gap-x-4 gap-y-1 border-b border-line px-3 py-1.5 text-[11px]"
        data-testid="price-chart-legend"
      >
        <span className="label normal-case tracking-normal text-ink-faint">probability</span>
        <span className="numeric text-ink">
          O <span className="text-ink-muted">{fmtProbability(legendCandle?.open)}</span>
        </span>
        <span className="numeric text-ink">
          H <span className="text-ink-muted">{fmtProbability(legendCandle?.high)}</span>
        </span>
        <span className="numeric text-ink">
          L <span className="text-ink-muted">{fmtProbability(legendCandle?.low)}</span>
        </span>
        <span className="numeric text-ink">
          C <span className="text-ink-muted">{fmtProbability(legendCandle?.close)}</span>
        </span>
        <span className="numeric text-ink">
          Vol <span className="text-ink-muted">{fmtUsd(legendBar?.volume_usd)}</span>
        </span>
        <span className="numeric text-ink">
          Fills{" "}
          <span className="text-ink-muted">
            {legendBar?.trade_count == null ? "not available" : legendBar.trade_count}
          </span>
        </span>
        <span className="text-ink-faint">
          {hovered ? "Bar" : "Last bar"} {fmtTimestamp(legendBar?.ts_event, false)}
        </span>
        {stale ? (
          <span className="badge-warn" title="At least one bar is flagged stale by the source">
            stale data
          </span>
        ) : null}
        {indicators.length > 0 ? (
          <span className="ml-auto flex flex-wrap gap-2 text-[10px] text-ink-muted">
            {indicators.map((indicator) => (
              <span key={indicator.id} className="inline-flex items-center gap-1">
                <span
                  aria-hidden="true"
                  className="inline-block h-1.5 w-3 rounded-sm"
                  style={{ background: indicator.color }}
                />
                {indicator.label}
                {indicator.warmup != null ? (
                  <span className="text-ink-faint">warm-up {indicator.warmup} bars</span>
                ) : null}
              </span>
            ))}
          </span>
        ) : null}
      </div>

      <div className="relative">
        <div
          ref={containerRef}
          className="w-full"
          style={{ height }}
          role="img"
          aria-label={`Price chart with ${candle.data.length} bars${
            lifecyclePhaseAvailable(lifecycle) ? ", lifecycle phases shaded" : ""
          }`}
        />
        <div ref={overlayRef} className="pointer-events-none absolute inset-0" aria-hidden="true">
          {overlay.map((band) => (
            <div
              key={band.key}
              title={band.label}
              className="absolute"
              style={{
                left: band.left,
                width: band.width,
                background: band.color,
                top: band.kind === "position" ? undefined : 0,
                bottom: 0,
                height: band.kind === "position" ? 16 : undefined,
              }}
            />
          ))}
        </div>
      </div>

      {separateIndicators.length > 0 ? (
        <div className="border-t border-line">
          <div className="flex items-center justify-between px-3 py-1 text-[10px] text-ink-faint">
            <span>Separate panes — server-computed series</span>
            <span>one pane per indicator, own price scale</span>
          </div>
          {separateIndicators.map((indicator) => (
            <div key={indicator.id} className="border-t border-line first:border-t-0">
              <div className="flex items-center justify-between gap-2 px-3 py-1 text-[10px] text-ink-muted">
                <span className="inline-flex items-center gap-1.5">
                  <span
                    aria-hidden="true"
                    className="inline-block h-1.5 w-3 rounded-sm"
                    style={{ background: outputLineColor(indicator.color, 0) }}
                  />
                  <span className="text-ink">{indicator.label}</span>
                  {indicator.warmup != null ? (
                    <span className="text-ink-faint">warm-up {indicator.warmup} bars</span>
                  ) : null}
                </span>
                <span className="flex flex-wrap gap-2">
                  {indicator.lines.map((line, index) => (
                    <span key={line.key} className="inline-flex items-center gap-1">
                      <span
                        aria-hidden="true"
                        className="inline-block h-1.5 w-3 rounded-sm"
                        style={{ background: outputLineColor(indicator.color, index) }}
                      />
                      {outputLineTitle(indicator.label, line.label)}
                    </span>
                  ))}
                </span>
              </div>
              <div
                ref={(element) => {
                  if (element) separateContainers.current.set(indicator.id, element);
                  else separateContainers.current.delete(indicator.id);
                }}
                data-testid={`separate-pane-${indicator.id}`}
                className="w-full"
              />
            </div>
          ))}
        </div>
      ) : null}

      {lifecyclePhaseAvailable(lifecycle) ? (
        <div className="flex flex-wrap items-center gap-2 border-t border-line px-3 py-1 text-[10px] text-ink-faint">
          <span>
            Kickoff {fmtTimestamp(lifecycle?.actual_kickoff_utc)} · end{" "}
            {fmtTimestamp(lifecycle?.actual_end_utc)}
          </span>
          <span>
            observed duration{" "}
            {fmtDuration(
              lifecycle?.actual_kickoff_utc && lifecycle?.actual_end_utc
                ? (Date.parse(lifecycle.actual_end_utc) -
                    Date.parse(lifecycle.actual_kickoff_utc)) /
                    1000
                : null,
            )}
          </span>
          <span className="flex items-center gap-2">
            {(
              [
                ["pre_match", CHART_COLORS.preMatch],
                ["in_play", CHART_COLORS.inPlay],
                ["post_match", CHART_COLORS.postMatch],
                ["settled", CHART_COLORS.settled],
              ] as const
            ).map(([phase, color]) => (
              <span key={phase} className="inline-flex items-center gap-1">
                <span
                  aria-hidden="true"
                  className="inline-block h-2 w-3 rounded-sm border border-line"
                  style={{ background: color }}
                />
                {PHASE_LABELS[phase]}
              </span>
            ))}
          </span>
          <span className="ml-auto">
            {phaseFilter === "all" ? "all phases shaded" : `${PHASE_LABELS[phaseFilter]} shaded`}
          </span>
        </div>
      ) : (
        <div className="border-t border-line px-3 py-1 text-[10px] text-ink-faint">
          Phase unknown — no verified lifecycle row. No phase shading and no nominal duration is
          substituted.
        </div>
      )}
    </div>
  );
}
