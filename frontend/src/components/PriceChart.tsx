import { useEffect, useMemo, useRef, useState } from "react";
import {
  ColorType,
  CrosshairMode,
  createChart,
  type IChartApi,
  type ISeriesApi,
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
  /** Preview state for a separate pane; an empty pane shows it instead of a blank chart. */
  status?: "loading" | "empty" | "ready";
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
  /** Show goal markers; halftime and kickoff/end stay visible either way. */
  showGoals?: boolean;
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
 * Allocate one colour per output line across every indicator in the chart.
 * The first line prefers its instance colour; every other line takes the first
 * palette entry not used anywhere yet, so two indicators that were handed the
 * same instance colour cannot paint the same hue. When the palette is
 * exhausted the entries cycle deterministically by allocation order.
 */
export function allocateLineColors(
  indicators: ChartIndicatorSeries[],
): Map<string, string[]> {
  const used = new Set<string>();
  const allocated = new Map<string, string[]>();
  let count = 0;
  for (const indicator of indicators) {
    const colors: string[] = [];
    indicator.lines.forEach((_, index) => {
      const color =
        index === 0 && !used.has(indicator.color.toLowerCase())
          ? indicator.color
          : (INDICATOR_PALETTE.find((candidate) => !used.has(candidate.toLowerCase())) ??
            INDICATOR_PALETTE[count % INDICATOR_PALETTE.length]);
      used.add(color.toLowerCase());
      colors.push(color);
      count += 1;
    });
    allocated.set(indicator.id, colors);
  }
  return allocated;
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
  showGoals = true,
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
  // Bumped after every chart creation so marker application re-runs against the
  // series that now exists; markers are never applied from the build effect.
  const [chartGeneration, setChartGeneration] = useState(0);

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
  // A selected separate-pane indicator owns its pane whether or not values have
  // arrived; only its lines are conditional.
  const separateIndicators = indicators.filter((indicator) => indicator.pane === "separate");
  const lineColors = useMemo(() => allocateLineColors(indicators), [indicators]);

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

  const lifecycleSignature = useMemo(
    () =>
      lifecycle
        ? `${lifecycle.actual_kickoff_utc ?? ""}|${lifecycle.actual_end_utc ?? ""}|${lifecycle.settlement_ts ?? ""}|${(lifecycle.events ?? [])
            .map((event) => `${event.ts}:${event.type}`)
            .join(",")}`
        : "",
    [lifecycle],
  );

  // Markers are data, not chart structure: a new trade list or a goals toggle
  // re-applies them on the existing candle series instead of rebuilding.
  const markers = useMemo(
    () =>
      runChartMarkers({
        trades,
        signals,
        lifecycle,
        events: lifecycle?.events ?? null,
        showGoals,
      }).filter((marker) => Number.isFinite(marker.time)),
    [trades, signals, lifecycle, showGoals],
  );
  // The click handler reads this ref, so hit-testing always sees the current
  // list even though the subscription is installed only when the chart is built.
  const markersRef = useRef(markers);
  markersRef.current = markers;

  const candleFirstTime = candle.data[0]?.time ?? null;
  const candleLastTime = candle.data[candle.data.length - 1]?.time ?? null;
  const visibleMarkers = useMemo<SeriesMarker<Time>[]>(() => {
    if (candleFirstTime === null || candleLastTime === null) return [];
    const tickets = markers.filter(
      (marker) => marker.time >= candleFirstTime && marker.time <= candleLastTime,
    );
    // Dense runs keep the arrows but drop labels; settlement stays explicit.
    const compactMarkers = tickets.length > 24;
    return tickets.map((marker) => ({
      time: marker.time as UTCTimestamp,
      position: marker.position,
      color: marker.color,
      shape: marker.shape,
      size: compactMarkers ? 0.6 : 1,
      text:
        compactMarkers && marker.kind !== "settlement" && !marker.id.startsWith("lifecycle:")
          ? ""
          : marker.text,
      id: marker.id,
    })) as SeriesMarker<Time>[];
  }, [markers, candleFirstTime, candleLastTime]);

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
      const colors = lineColors.get(indicator.id) ?? [];
      indicator.lines.forEach((line, index) => {
        if (line.points.length === 0) return;
        const lineSeries = chart.addLineSeries({
          color: colors[index] ?? indicator.color,
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
    // Markers are applied by the marker effect below, so a goals toggle or a
    // new trade list never rebuilds this chart.

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
      const hit = markersRef.current.find(
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
    // scale and ResizeObserver. Panes cannot scroll or scale on their own, so
    // alignment follows the main chart's visible *time* range: panes have
    // different series lengths after warm-up, so a logical range would skew.
    const paneCharts: IChartApi[] = [];
    const paneObservers: ResizeObserver[] = [];

    for (const indicator of separateIndicators) {
      if (indicator.lines.length === 0) continue;
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
      const colors = lineColors.get(indicator.id) ?? [];
      indicator.lines.forEach((line, index) => {
        if (line.points.length === 0) return;
        const series = pane.addLineSeries({
          color: colors[index] ?? indicator.color,
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
    }

    // Apply the main chart's initial window to every pane, then keep them in
    // step on every subsequent visible-time-range change. If the main range is
    // not readable yet, fit once and read again rather than leaving panes to
    // invent their own span.
    const applyMainVisibleRange = (range: { from: Time; to: Time } | null) => {
      if (!range) return;
      for (const pane of paneCharts) pane.timeScale().setVisibleRange(range);
    };
    let subscribedToMainRange = false;
    if (paneCharts.length > 0) {
      let initialRange = chart.timeScale().getVisibleRange();
      if (!initialRange) {
        chart.timeScale().fitContent();
        initialRange = chart.timeScale().getVisibleRange();
      }
      applyMainVisibleRange(initialRange);
      chart.timeScale().subscribeVisibleTimeRangeChange(applyMainVisibleRange);
      subscribedToMainRange = true;
    }

    // The candle series now exists; let the marker effect paint the current
    // marker list onto it (including after a rebuild).
    setChartGeneration((generation) => generation + 1);

    return () => {
      resizeObserver.disconnect();
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(updateBands);
      if (subscribedToMainRange) {
        chart.timeScale().unsubscribeVisibleTimeRangeChange(applyMainVisibleRange);
      }
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
    // which keeps a research view reproducible rather than sticky. Trades,
    // signals and the goals toggle are absent on purpose: they only change the
    // marker list, which the marker effect below applies in place.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    dataSignature,
    indicatorSignature,
    lifecycleSignature,
    positionSignature,
    theme,
    height,
    phaseFilter,
  ]);

  // Marker updates ride on the existing candle series: toggling goals must not
  // recreate the chart, re-fit the range or clear the cursor.
  useEffect(() => {
    candleRef.current?.setMarkers(visibleMarkers);
  }, [visibleMarkers, chartGeneration]);

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
          <span className="ml-auto flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-ink-muted">
            {indicators.map((indicator) => {
              const colors = lineColors.get(indicator.id) ?? [];
              return (
                <span
                  key={indicator.id}
                  data-testid={`legend-group-${indicator.id}`}
                  className="inline-flex flex-wrap items-center gap-x-2 gap-y-1"
                >
                  {indicator.lines.length === 0 ? (
                    <span className="inline-flex items-center gap-1">
                      <span
                        aria-hidden="true"
                        className="inline-block h-1.5 w-3 rounded-sm"
                        style={{ background: indicator.color }}
                        data-color={indicator.color}
                      />
                      {indicator.label}
                    </span>
                  ) : (
                    indicator.lines.map((line, index) => {
                      const color = colors[index] ?? indicator.color;
                      return (
                        <span
                          key={line.key}
                          data-testid={`legend-line-${indicator.id}-${line.key}`}
                          className="inline-flex items-center gap-1"
                        >
                          <span
                            aria-hidden="true"
                            className="inline-block h-1.5 w-3 rounded-sm"
                            style={{ background: color }}
                            data-color={color}
                          />
                          {outputLineTitle(indicator.label, line.label)}
                        </span>
                      );
                    })
                  )}
                  {indicator.warmup != null ? (
                    <span className="text-ink-faint">warm-up {indicator.warmup} bars</span>
                  ) : null}
                </span>
              );
            })}
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
          {separateIndicators.map((indicator) => {
            const colors = lineColors.get(indicator.id) ?? [];
            return (
              <div key={indicator.id} className="border-t border-line first:border-t-0">
                <div className="flex items-center justify-between gap-2 px-3 py-1 text-[10px] text-ink-muted">
                  <span className="inline-flex items-center gap-1.5">
                    <span
                      aria-hidden="true"
                      className="inline-block h-1.5 w-3 rounded-sm"
                      style={{ background: colors[0] ?? indicator.color }}
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
                          style={{ background: colors[index] ?? indicator.color }}
                        />
                        {outputLineTitle(indicator.label, line.label)}
                      </span>
                    ))}
                  </span>
                </div>
                {indicator.lines.length === 0 ? (
                  <div
                    data-testid={`separate-pane-message-${indicator.id}`}
                    className="px-3 py-2 text-[10px] text-ink-faint"
                  >
                    {indicator.status === "loading"
                      ? "Awaiting server values"
                      : "No values in this range"}
                  </div>
                ) : (
                  <div
                    ref={(element) => {
                      if (element) separateContainers.current.set(indicator.id, element);
                      else separateContainers.current.delete(indicator.id);
                    }}
                    data-testid={`separate-pane-${indicator.id}`}
                    className="w-full"
                  />
                )}
              </div>
            );
          })}
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
