import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import type { Bar, LifecycleMeta, Trade } from "../api/types";
import type { ChartIndicatorSeries } from "./PriceChart";
import { PriceChart } from "./PriceChart";
import { ThemeProvider } from "../state/theme";

const chartState = vi.hoisted(() => ({
  markers: [] as Array<{ text?: string; id?: string }>,
  candleTimes: [] as number[],
  volumeTimes: [] as number[],
  lineSeriesOptions: [] as Array<Record<string, unknown>>,
  createChartCount: 0,
  fitContentCalls: 0,
  crosshairHandlers: [] as Array<(param: { time?: unknown }) => void>,
  mainVisibleRange: null as { from: number; to: number } | null,
  setVisibleRangeCalls: [] as Array<{ index: number; range: { from: number; to: number } }>,
  logicalCalls: [] as number[],
  visibleTimeRangeHandlers: [] as Array<
    ((range: { from: number; to: number } | null) => void) | undefined
  >,
}));

vi.mock("lightweight-charts", () => {
  // Map a time to the mocked 600 px plot, linear over the visible range.
  const timeToX = (time: number): number | null => {
    const times = chartState.candleTimes;
    const range = chartState.mainVisibleRange;
    if (times.length === 0) return null;
    const from = range ? range.from : times[0];
    const to = range ? range.to : times[times.length - 1];
    if (to <= from) return null;
    return ((time - from) / (to - from)) * 600;
  };
  const makeSeries = (kind: "candle" | "volume" | "line") => {
    const series = {
      setData: (data: Array<{ time: number }>) => {
        if (kind === "candle") chartState.candleTimes = data.map((point) => point.time);
        if (kind === "volume") chartState.volumeTimes = data.map((point) => point.time);
      },
      setMarkers: (markers: Array<{ text?: string; id?: string }>) => {
        if (kind === "candle") chartState.markers = markers;
      },
      priceScale: () => ({ applyOptions: () => {} }),
    };
    return series;
  };
  const makeChart = (index: number) => {
    const timeScale = {
      fitContent: () => {
        chartState.fitContentCalls += 1;
      },
      width: () => 600,
      getVisibleRange: () => chartState.mainVisibleRange,
      setVisibleRange: (range: { from: number; to: number }) => {
        chartState.setVisibleRangeCalls.push({ index, range });
      },
      // Like the library: only a real data time resolves; a goal wallclock with
      // seconds that no bar starts at returns null.
      timeToCoordinate: (time: number) => {
        if (!chartState.candleTimes.includes(time)) return null;
        return timeToX(time);
      },
      // Logical indices are bar indices; interpolate between bar times so a
      // fractional index inside a gap lands between its bracketing bars.
      logicalToCoordinate: (logical: number) => {
        const times = chartState.candleTimes;
        if (times.length === 0) return null;
        chartState.logicalCalls.push(logical);
        const low = Math.floor(logical);
        const high = Math.ceil(logical);
        if (low < 0 || high > times.length - 1) return null;
        const time = times[low] + (logical - low) * (times[high] - times[low]);
        return timeToX(time);
      },
      subscribeVisibleLogicalRangeChange: () => {},
      unsubscribeVisibleLogicalRangeChange: () => {},
      setVisibleLogicalRange: () => {},
      subscribeVisibleTimeRangeChange: (
        handler: (range: { from: number; to: number } | null) => void,
      ) => {
        chartState.visibleTimeRangeHandlers.push(handler);
      },
      unsubscribeVisibleTimeRangeChange: (
        handler: (range: { from: number; to: number } | null) => void,
      ) => {
        chartState.visibleTimeRangeHandlers = chartState.visibleTimeRangeHandlers.filter(
          (candidate) => candidate !== handler,
        );
      },
    };
    return {
      addCandlestickSeries: () => makeSeries("candle"),
      addHistogramSeries: () => makeSeries("volume"),
      addLineSeries: (options: Record<string, unknown>) => {
        chartState.lineSeriesOptions.push(options ?? {});
        return makeSeries("line");
      },
      timeScale: () => timeScale,
      subscribeClick: () => {},
      subscribeCrosshairMove: (handler: (param: { time?: unknown }) => void) => {
        chartState.crosshairHandlers.push(handler);
      },
      unsubscribeCrosshairMove: () => {},
      remove: () => {},
      applyOptions: () => {},
    };
  };
  return {
    createChart: () => {
      const index = chartState.createChartCount;
      chartState.createChartCount += 1;
      return makeChart(index);
    },
    ColorType: { Solid: "solid" },
    CrosshairMode: { Normal: 0 },
  };
});

const bars: Bar[] = [
  {
    ts_event: "2024-10-19T10:00:00Z",
    open: 0.4,
    high: 0.42,
    low: 0.39,
    close: 0.41,
    volume_usd: 120,
    trade_count: 4,
  },
  {
    ts_event: "2024-10-19T10:05:00Z",
    open: 0.41,
    high: 0.43,
    low: 0.4,
    close: 0.42,
    volume_usd: 90,
    trade_count: 3,
  },
  {
    ts_event: "2024-10-19T12:00:00Z",
    open: 0.42,
    high: 0.44,
    low: 0.41,
    close: 0.43,
    volume_usd: 60,
    trade_count: 2,
  },
];

const seconds = (iso: string) => Math.floor(Date.parse(iso) / 1000);

const trade: Trade = {
  trade_id: "T-0001",
  side: "BUY",
  close_side: "SELL",
  token: "YES",
  entry_ts: "2024-10-19T10:00:00Z",
  exit_ts: "2024-10-19T10:05:00Z",
  entry_price: 0.4,
  exit_price: 0.42,
  gross_pnl_usd: 2,
  net_pnl_usd: 1.8,
  fees_usd: 0.2,
};

const lifecycleWithEvents: LifecycleMeta = {
  actual_kickoff_utc: "2024-10-19T10:00:00Z",
  actual_end_utc: "2024-10-19T12:00:00Z",
  events: [
    {
      ts: "2024-10-19T10:55:00Z",
      type: "goal",
      label: "GOAL",
      team: "Bournemouth",
      detail: "Goal! Bournemouth 1-0 Arsenal",
    },
    {
      ts: "2024-10-19T11:05:00Z",
      type: "halftime",
      label: "HALF TIME",
      team: null,
      detail: null,
    },
  ],
};

// The bars are 5-minute spaced at the start, so 10:00..10:05 is one interval
// and anything after 10:10 (like the 10:55 goal below) is a gap.
const lifecycleGoalGap = lifecycleWithEvents;

const lifecycleGoalInsideBar: LifecycleMeta = {
  actual_kickoff_utc: "2024-10-19T10:00:00Z",
  actual_end_utc: "2024-10-19T12:00:00Z",
  events: [
    {
      ts: "2024-10-19T10:02:00Z",
      type: "goal",
      label: "GOAL",
      team: "Bournemouth",
      detail: null,
    },
    {
      ts: "2024-10-19T11:05:00Z",
      type: "halftime",
      label: "HALF TIME",
      team: null,
      detail: null,
    },
  ],
};

const lifecycleGoalWithoutTeam: LifecycleMeta = {
  actual_kickoff_utc: "2024-10-19T10:00:00Z",
  actual_end_utc: "2024-10-19T12:00:00Z",
  events: [
    { ts: "2024-10-19T10:02:00Z", type: "goal", label: "GOAL", team: null, detail: null },
  ],
};

const liveMatchState: LifecycleMeta = {
  actual_kickoff_utc: "2024-10-19T10:00:00Z",
  actual_end_utc: "2024-10-19T12:00:00Z",
  events: [
    {
      ts: "2024-10-19T10:00:00Z",
      type: "goal",
      label: "GOAL",
      team: "Bournemouth",
      detail: null,
    },
    {
      ts: "2024-10-19T11:30:00Z",
      type: "goal",
      label: "GOAL",
      team: "Arsenal",
      detail: null,
    },
    {
      ts: "2024-10-19T11:05:00Z",
      type: "halftime",
      label: "HALF TIME",
      team: null,
      detail: null,
    },
  ],
  match_state: {
    goals: [
      {
        ts: "2024-10-19T10:00:00Z",
        type: "goal",
        label: "GOAL",
        team: "Bournemouth",
        detail: null,
      },
      {
        ts: "2024-10-19T11:30:00Z",
        type: "goal",
        label: "GOAL",
        team: "Arsenal",
        detail: null,
      },
    ],
    corners: [
      { ts: "2024-10-19T10:05:00Z", team: "Manchester City" },
      { ts: "2024-10-19T12:30:00Z", team: "Manchester City" },
    ],
  },
};

const unavailableMatchState: LifecycleMeta = {
  ...liveMatchState,
  match_state: { goals: null, corners: null },
};

const macd: ChartIndicatorSeries = {
  id: "macd",
  label: "MACD",
  color: "#56B4E9",
  pane: "overlay",
  lines: [
    { key: "value", label: "Value", points: [{ time: seconds(bars[0].ts_event), value: 0.01 }] },
    { key: "signal", label: "Signal", points: [{ time: seconds(bars[0].ts_event), value: 0.02 }] },
    {
      key: "histogram",
      label: "Histogram",
      points: [{ time: seconds(bars[0].ts_event), value: -0.01 }],
    },
  ],
};

const rsi: ChartIndicatorSeries = {
  id: "rsi",
  label: "RSI",
  color: "#E69F00",
  pane: "separate",
  lines: [{ key: "value", label: "Value", points: [{ time: seconds(bars[0].ts_event), value: 55.5 }] }],
};

const macdSeparate: ChartIndicatorSeries = { ...macd, pane: "separate" };

const bollinger: ChartIndicatorSeries = {
  id: "bollinger",
  label: "Bollinger",
  color: "#56B4E9",
  pane: "overlay",
  lines: [
    { key: "upper", label: "Upper", points: [{ time: seconds(bars[0].ts_event), value: 0.5 }] },
    { key: "middle", label: "Middle", points: [{ time: seconds(bars[0].ts_event), value: 0.45 }] },
    { key: "lower", label: "Lower", points: [{ time: seconds(bars[0].ts_event), value: 0.4 }] },
  ],
};

const sma: ChartIndicatorSeries = {
  id: "sma",
  label: "SMA",
  color: "#56B4E9",
  pane: "overlay",
  lines: [{ key: "value", label: "SMA", points: [{ time: seconds(bars[0].ts_event), value: 0.43 }] }],
};

function renderChart(lifecycle: LifecycleMeta | null) {
  return render(
    <ThemeProvider>
      <PriceChart bars={bars} lifecycle={lifecycle} trades={[trade]} signals={[]} />
    </ThemeProvider>,
  );
}

describe("price chart markers", () => {
  beforeEach(() => {
    chartState.markers = [];
    chartState.candleTimes = [];
    chartState.volumeTimes = [];
    chartState.lineSeriesOptions = [];
    chartState.createChartCount = 0;
    chartState.fitContentCalls = 0;
    chartState.crosshairHandlers = [];
    chartState.mainVisibleRange = {
      from: seconds(bars[0].ts_event),
      to: seconds(bars[2].ts_event),
    };
    chartState.setVisibleRangeCalls = [];
    chartState.logicalCalls = [];
    chartState.visibleTimeRangeHandlers = [];
  });

  it("renders candles and volume from the supplied bars only", () => {
    renderChart(null);
    expect(chartState.candleTimes).toEqual(bars.map((bar) => seconds(bar.ts_event)));
    expect(chartState.volumeTimes).toEqual(chartState.candleTimes);
  });

  it("shows a clear unknown-phase badge and no lifecycle markers", () => {
    renderChart(null);
    expect(screen.getByText(/Phase unknown/i)).toBeInTheDocument();
    expect(chartState.markers.map((marker) => marker.id)).toEqual([
      "T-0001:entry",
      "T-0001:exit",
    ]);
    expect(chartState.markers.some((marker) => marker.id?.startsWith("lifecycle:"))).toBe(false);
  });

  it("adds kickoff and end markers only once both observed wallclocks exist", () => {
    renderChart({
      actual_kickoff_utc: "2024-10-19T10:00:00Z",
      actual_end_utc: "2024-10-19T12:00:00Z",
    });
    const ids = chartState.markers.map((marker) => marker.id);
    expect(ids).toContain("lifecycle:kickoff");
    expect(ids).toContain("lifecycle:end");
    expect(screen.queryByText(/Phase unknown/i)).toBeNull();
  });

  it("labels halftime and goals, and hides only the goals when showGoals is false", () => {
    const lifecycle = lifecycleWithEvents;
    const view = render(
      <ThemeProvider>
        <PriceChart bars={bars} lifecycle={lifecycle} trades={[trade]} signals={[]} />
      </ThemeProvider>,
    );
    expect(chartState.markers.map((marker) => marker.text)).toEqual(
      expect.arrayContaining(["GOAL", "HALF TIME", "KICKOFF", "END"]),
    );

    view.rerender(
      <ThemeProvider>
        <PriceChart
          bars={bars}
          lifecycle={lifecycle}
          trades={[trade]}
          signals={[]}
          showGoals={false}
        />
      </ThemeProvider>,
    );
    const texts = chartState.markers.map((marker) => marker.text);
    expect(texts).not.toContain("GOAL");
    expect(texts).toEqual(expect.arrayContaining(["HALF TIME", "KICKOFF", "END"]));
  });

  it("toggles goals on the existing chart without rebuilding or clearing the cursor", () => {
    const view = render(
      <ThemeProvider>
        <PriceChart
          bars={bars}
          lifecycle={lifecycleWithEvents}
          trades={[trade]}
          signals={[]}
        />
      </ThemeProvider>,
    );
    expect(chartState.createChartCount).toBe(1);
    const texts = () => chartState.markers.map((marker) => marker.text);
    expect(texts()).toEqual(expect.arrayContaining(["GOAL", "HALF TIME", "KICKOFF", "END"]));

    const handler = chartState.crosshairHandlers[0];
    act(() => handler?.({ time: seconds(bars[1].ts_event) }));
    expect(screen.getByTestId("price-chart-legend").textContent).toContain(
      "Bar 2024-10-19 10:05 UTC",
    );

    view.rerender(
      <ThemeProvider>
        <PriceChart
          bars={bars}
          lifecycle={lifecycleWithEvents}
          trades={[trade]}
          signals={[]}
          showGoals={false}
        />
      </ThemeProvider>,
    );

    // The goals toggle only re-applies markers: no new chart, no new cursor
    // subscription, and the hovered bar stays in the legend.
    expect(chartState.createChartCount).toBe(1);
    expect(chartState.crosshairHandlers).toHaveLength(1);
    expect(texts()).not.toContain("GOAL");
    expect(texts()).toEqual(expect.arrayContaining(["HALF TIME", "KICKOFF", "END"]));
    expect(screen.getByTestId("price-chart-legend").textContent).toContain(
      "Bar 2024-10-19 10:05 UTC",
    );
  });

  it("renders an empty position-window list without inventing shading or markers", () => {
    render(
      <ThemeProvider>
        <PriceChart
          bars={bars}
          lifecycle={null}
          trades={[]}
          signals={[]}
          positions={[]}
        />
      </ThemeProvider>,
    );
    expect(chartState.markers).toEqual([]);
    expect(screen.getByRole("img", { name: /Price chart with 3 bars/ })).toBeInTheDocument();
  });

  it("shows the hovered bar in the OHLC strip and restores the last bar on leave", () => {
    renderChart(null);
    const legend = screen.getByTestId("price-chart-legend");
    expect(legend.textContent).toContain("Last bar 2024-10-19 12:00 UTC");
    expect(legend.textContent).toContain("Vol $60.00");
    expect(legend.textContent).toContain("Fills 2");

    const handler = chartState.crosshairHandlers[0];
    expect(handler).toBeTypeOf("function");
    const chartsBeforeHover = chartState.createChartCount;
    act(() => handler({ time: seconds(bars[1].ts_event) }));
    expect(chartState.createChartCount).toBe(chartsBeforeHover);

    expect(legend.textContent).toContain("Bar 2024-10-19 10:05 UTC");
    expect(legend.textContent).toContain("O 0.410");
    expect(legend.textContent).toContain("H 0.430");
    expect(legend.textContent).toContain("L 0.400");
    expect(legend.textContent).toContain("C 0.420");
    expect(legend.textContent).toContain("Vol $90.00");
    expect(legend.textContent).toContain("Fills 3");

    act(() => handler({ time: undefined }));
    expect(legend.textContent).toContain("Last bar 2024-10-19 12:00 UTC");
    expect(legend.textContent).toContain("Vol $60.00");
  });

  it("gives every MACD output its own title and colour", () => {
    render(
      <ThemeProvider>
        <PriceChart bars={bars} lifecycle={null} indicators={[macd]} />
      </ThemeProvider>,
    );
    const options = chartState.lineSeriesOptions;
    expect(options).toHaveLength(3);
    const titles = options.map((option) => option.title);
    expect(titles).toEqual(["MACD · Value", "MACD · Signal", "MACD · Histogram"]);
    expect(titles.join(" ")).toContain("MACD");
    expect(titles.join(" ")).toContain("Signal");
    expect(titles.join(" ")).toContain("Histogram");
    const colors = options.map((option) => option.color);
    expect(new Set(colors).size).toBe(3);
    expect(colors[0]).toBe("#56B4E9");
  });

  it("renders one chart per separate indicator on top of the main chart", () => {
    render(
      <ThemeProvider>
        <PriceChart bars={bars} lifecycle={null} indicators={[rsi, macdSeparate]} />
      </ThemeProvider>,
    );
    expect(chartState.createChartCount).toBe(3);
    expect(screen.getByTestId("separate-pane-rsi")).toBeInTheDocument();
    expect(screen.getByTestId("separate-pane-macd")).toBeInTheDocument();
    expect(screen.getByText(/server-computed series/i)).toBeInTheDocument();
    const titles = chartState.lineSeriesOptions.map((option) => option.title);
    expect(titles).toEqual([
      "RSI · Value",
      "MACD · Value",
      "MACD · Signal",
      "MACD · Histogram",
    ]);
  });

  it("renders a separate pane with no overlay indicator selected", () => {
    render(
      <ThemeProvider>
        <PriceChart bars={bars} lifecycle={null} indicators={[rsi]} />
      </ThemeProvider>,
    );
    expect(chartState.createChartCount).toBe(2);
    expect(screen.getByTestId("separate-pane-rsi")).toBeInTheDocument();
    expect(screen.getByText(/server-computed series/i)).toBeInTheDocument();
    expect(chartState.lineSeriesOptions.map((option) => option.title)).toEqual(["RSI · Value"]);
    expect(screen.queryByTestId("separate-pane-message-rsi")).toBeNull();
  });

  it("keeps the separate section and states an empty range without a chart", () => {
    const empty: ChartIndicatorSeries = { ...rsi, lines: [], status: "empty" };
    render(
      <ThemeProvider>
        <PriceChart bars={bars} lifecycle={null} indicators={[empty]} />
      </ThemeProvider>,
    );
    expect(chartState.createChartCount).toBe(1);
    expect(chartState.lineSeriesOptions).toEqual([]);
    expect(screen.getByText(/server-computed series/i)).toBeInTheDocument();
    expect(screen.queryByTestId("separate-pane-rsi")).toBeNull();
    expect(screen.getByTestId("separate-pane-message-rsi").textContent).toBe(
      "No values in this range",
    );
  });

  it("says it is awaiting server values while a separate pane has no preview", () => {
    const loading: ChartIndicatorSeries = { ...rsi, lines: [], status: "loading" };
    render(
      <ThemeProvider>
        <PriceChart bars={bars} lifecycle={null} indicators={[loading]} />
      </ThemeProvider>,
    );
    expect(chartState.createChartCount).toBe(1);
    expect(screen.getByTestId("separate-pane-message-rsi").textContent).toBe(
      "Awaiting server values",
    );
  });

  it("gives every separate pane the main visible range and follows later main changes", () => {
    render(
      <ThemeProvider>
        <PriceChart bars={bars} lifecycle={null} indicators={[rsi, macdSeparate]} />
      </ThemeProvider>,
    );
    const initial = {
      from: seconds(bars[0].ts_event),
      to: seconds(bars[2].ts_event),
    };
    const paneCalls = chartState.setVisibleRangeCalls.filter((call) => call.index !== 0);
    expect(paneCalls.map((call) => call.index)).toEqual([1, 2]);
    for (const call of paneCalls) expect(call.range).toEqual(initial);
    expect(chartState.visibleTimeRangeHandlers).toHaveLength(1);

    const before = chartState.setVisibleRangeCalls.length;
    const next = { from: seconds(bars[1].ts_event), to: seconds(bars[2].ts_event) };
    act(() => chartState.visibleTimeRangeHandlers[0]?.(next));
    const followCalls = chartState.setVisibleRangeCalls.slice(before);
    expect(followCalls.map((call) => call.index)).toEqual([1, 2]);
    for (const call of followCalls) expect(call.range).toEqual(next);
  });

  it("allocates unique line colours across indicators sharing an instance colour", () => {
    render(
      <ThemeProvider>
        <PriceChart bars={bars} lifecycle={null} indicators={[bollinger, sma]} />
      </ThemeProvider>,
    );
    const options = chartState.lineSeriesOptions;
    expect(options).toHaveLength(4);
    const colors = options.map((option) => option.color);
    expect(new Set(colors).size).toBe(4);
    expect(colors[0]).toBe("#56B4E9");

    const seriesColorByTitle = new Map(
      options.map((option) => [option.title, option.color]),
    );
    const legendColor = (testId: string) =>
      screen.getByTestId(testId).querySelector("[data-color]")?.getAttribute("data-color");
    expect(legendColor("legend-line-bollinger-upper")).toBe(
      seriesColorByTitle.get("Bollinger · Upper"),
    );
    expect(legendColor("legend-line-bollinger-middle")).toBe(
      seriesColorByTitle.get("Bollinger · Middle"),
    );
    expect(legendColor("legend-line-bollinger-lower")).toBe(
      seriesColorByTitle.get("Bollinger · Lower"),
    );
    expect(legendColor("legend-line-sma-value")).toBe(seriesColorByTitle.get("SMA"));
  });

  it("shows the data version in the footer and keeps lifecycle wallclocks out of it", () => {
    render(
      <ThemeProvider>
        <PriceChart
          bars={bars}
          lifecycle={lifecycleWithEvents}
          dataVersion="sha256:abc123def4567890"
        />
      </ThemeProvider>,
    );
    const footer = screen.getByTestId("price-chart-footer");
    expect(footer.textContent).toContain("data sha256:abc12…");
    expect(footer.textContent).toContain("in-play");
    expect(footer.textContent).not.toMatch(/Kickoff/);
    expect(footer.textContent).not.toMatch(/observed duration/);
  });

  it("re-fits the existing chart when resetKey changes without rebuilding it", () => {
    const view = render(
      <ThemeProvider>
        <PriceChart bars={bars} lifecycle={null} resetKey={0} />
      </ThemeProvider>,
    );
    expect(chartState.createChartCount).toBe(1);
    const fittedOnMount = chartState.fitContentCalls;

    view.rerender(
      <ThemeProvider>
        <PriceChart bars={bars} lifecycle={null} resetKey={1} />
      </ThemeProvider>,
    );

    expect(chartState.createChartCount).toBe(1);
    expect(chartState.fitContentCalls).toBe(fittedOnMount + 1);
  });

  it("draws a dashed goal line and a hit area when a goal falls in a bar gap", async () => {
    render(
      <ThemeProvider>
        <PriceChart
          bars={bars}
          lifecycle={lifecycleGoalGap}
          trades={[]}
          signals={[]}
          barIntervalSeconds={300}
        />
      </ThemeProvider>,
    );
    const hits = await screen.findAllByTestId("goal-hit");
    expect(hits).toHaveLength(1);
    const lines = screen.getAllByTestId("goal-line");
    expect(lines).toHaveLength(1);
    expect(lines[0].className).toContain("border-dashed");
    expect(lines[0].style.left).not.toBe("");
    expect(hits[0].getAttribute("aria-label")).toContain("Bournemouth");
    expect(hits[0].getAttribute("aria-label")).toContain("2024-10-19 10:55:00 UTC");
  });

  it("interpolates a goal in a gap in logical space because no bar starts at its time", async () => {
    render(
      <ThemeProvider>
        <PriceChart
          bars={bars}
          lifecycle={lifecycleGoalGap}
          trades={[]}
          signals={[]}
          barIntervalSeconds={300}
        />
      </ThemeProvider>,
    );
    const [line] = await screen.findAllByTestId("goal-line");
    // 10:55 is 50 of the 115 minutes between the 10:05 bar and the 12:00 bar,
    // so logical 1 + 3000/6900 lands at x 275 on the mocked 600 px plot even
    // though the library cannot resolve the goal time itself.
    expect(line.style.left).toBe("275px");
    expect(
      chartState.logicalCalls.some((logical) => Math.abs(logical - (1 + 3000 / 6900)) < 1e-9),
    ).toBe(true);
  });

  it("uses the covering bar's coordinate for a goal inside its interval", async () => {
    render(
      <ThemeProvider>
        <PriceChart
          bars={bars}
          lifecycle={lifecycleGoalInsideBar}
          trades={[]}
          signals={[]}
          barIntervalSeconds={300}
        />
      </ThemeProvider>,
    );
    const hits = await screen.findAllByTestId("goal-hit");
    expect(hits).toHaveLength(1);
    // The 10:00 bar owns 10:02, so the goal sits on that bar's coordinate.
    expect(hits[0].style.left).toBe("0px");
    expect(screen.queryAllByTestId("goal-line")).toHaveLength(0);
    expect(chartState.logicalCalls).toHaveLength(0);
  });

  it("skips a goal whose coordinate falls outside the plot", async () => {
    chartState.mainVisibleRange = {
      from: seconds(bars[0].ts_event),
      to: seconds(bars[1].ts_event),
    };
    render(
      <ThemeProvider>
        <PriceChart
          bars={bars}
          lifecycle={lifecycleGoalGap}
          trades={[]}
          signals={[]}
          barIntervalSeconds={300}
        />
      </ThemeProvider>,
    );
    expect(await screen.findByTestId("price-chart-legend")).toBeInTheDocument();
    expect(screen.queryAllByTestId("goal-hit")).toHaveLength(0);
    expect(screen.queryAllByTestId("goal-line")).toHaveLength(0);
  });

  it("gives a goal inside a bar interval a hit area but no second line", async () => {
    render(
      <ThemeProvider>
        <PriceChart
          bars={bars}
          lifecycle={lifecycleGoalInsideBar}
          trades={[]}
          signals={[]}
          barIntervalSeconds={300}
        />
      </ThemeProvider>,
    );
    const hits = await screen.findAllByTestId("goal-hit");
    expect(hits).toHaveLength(1);
    expect(screen.queryAllByTestId("goal-line")).toHaveLength(0);
  });

  it("keeps the goal line non-interactive and confines the hit area to the flag", async () => {
    render(
      <ThemeProvider>
        <PriceChart
          bars={bars}
          lifecycle={lifecycleGoalGap}
          trades={[]}
          signals={[]}
          barIntervalSeconds={300}
        />
      </ThemeProvider>,
    );
    const [hit] = await screen.findAllByTestId("goal-hit");
    // A short flag at the top leaves the plot free to pan and crosshair.
    expect(hit.style.height).toBe("16px");
    expect(hit.style.top).toBe("0px");
    expect(Number.parseFloat(hit.style.width)).toBeLessThanOrEqual(16);
    expect(hit.getAttribute("tabindex")).toBe("0");
    expect(hit.getAttribute("role")).not.toBe("button");
    const [line] = screen.getAllByTestId("goal-line");
    expect(line.className).toContain("pointer-events-none");
  });

  it("shows the goal team and formatted time on hover or focus and hides on leave", async () => {
    render(
      <ThemeProvider>
        <PriceChart bars={bars} lifecycle={lifecycleGoalGap} trades={[]} signals={[]} />
      </ThemeProvider>,
    );
    const [hit] = await screen.findAllByTestId("goal-hit");
    fireEvent.mouseEnter(hit);
    expect(screen.getByTestId("goal-popup").textContent).toContain("Bournemouth");
    expect(screen.getByTestId("goal-popup").textContent).toContain("2024-10-19 10:55:00 UTC");
    fireEvent.mouseLeave(hit);
    expect(screen.queryByTestId("goal-popup")).toBeNull();

    fireEvent.focusIn(hit);
    expect(screen.getByTestId("goal-popup").textContent).toContain("Bournemouth");
    fireEvent.focusOut(hit);
    expect(screen.queryByTestId("goal-popup")).toBeNull();
  });

  it("never invents a team for a goal that names none", async () => {
    render(
      <ThemeProvider>
        <PriceChart bars={bars} lifecycle={lifecycleGoalWithoutTeam} trades={[]} signals={[]} />
      </ThemeProvider>,
    );
    const [hit] = await screen.findAllByTestId("goal-hit");
    fireEvent.mouseEnter(hit);
    const popup = screen.getByTestId("goal-popup");
    expect(popup.textContent).toContain("team not available");
    expect(hit.getAttribute("aria-label")).toContain("team not available");
  });

  it("hides goal lines, hit areas and popups with showGoals false but keeps halftime", async () => {
    const view = render(
      <ThemeProvider>
        <PriceChart
          bars={bars}
          lifecycle={lifecycleGoalGap}
          trades={[trade]}
          signals={[]}
        />
      </ThemeProvider>,
    );
    const [hit] = await screen.findAllByTestId("goal-hit");
    expect(screen.getAllByTestId("goal-line")).toHaveLength(1);
    fireEvent.mouseEnter(hit);
    expect(screen.getByTestId("goal-popup")).toBeInTheDocument();

    view.rerender(
      <ThemeProvider>
        <PriceChart
          bars={bars}
          lifecycle={lifecycleGoalGap}
          trades={[trade]}
          signals={[]}
          showGoals={false}
        />
      </ThemeProvider>,
    );

    expect(screen.queryAllByTestId("goal-line")).toHaveLength(0);
    expect(screen.queryAllByTestId("goal-hit")).toHaveLength(0);
    expect(screen.queryByTestId("goal-popup")).toBeNull();
    const texts = chartState.markers.map((marker) => marker.text);
    expect(texts).not.toContain("GOAL");
    expect(texts).toEqual(expect.arrayContaining(["HALF TIME", "KICKOFF", "END"]));
    // The toggle still never rebuilds the chart.
    expect(chartState.createChartCount).toBe(1);
  });

  it("counts goals and corners up to the hovered bar and back to the last bar", () => {
    render(
      <ThemeProvider>
        <PriceChart bars={bars} lifecycle={liveMatchState} trades={[]} signals={[]} />
      </ThemeProvider>,
    );
    const chip = () => screen.getByTestId("match-state");
    expect(chip().textContent).toContain("Goals 2");
    expect(chip().textContent).toContain("Corners 1");
    expect(chip().getAttribute("title")).toContain("last bar");

    const handler = chartState.crosshairHandlers[0];
    act(() => handler?.({ time: seconds(bars[1].ts_event) }));
    expect(chip().textContent).toContain("Goals 1");
    expect(chip().textContent).toContain("Corners 1");
    expect(chip().getAttribute("title")).toContain("hovered bar");

    act(() => handler?.({ time: seconds(bars[0].ts_event) }));
    expect(chip().textContent).toContain("Goals 1");
    expect(chip().textContent).toContain("Corners 0");

    act(() => handler?.({ time: undefined }));
    expect(chip().textContent).toContain("Goals 2");
    expect(chip().textContent).toContain("Corners 1");
  });

  it("counts a goal or corner inside the hovered bar's interval, not just at its start", () => {
    render(
      <ThemeProvider>
        <PriceChart
          bars={bars}
          lifecycle={liveMatchState}
          trades={[]}
          signals={[]}
          barIntervalSeconds={300}
        />
      </ThemeProvider>,
    );
    const chip = () => screen.getByTestId("match-state");
    const handler = chartState.crosshairHandlers[0];
    // The 10:00 bar covers 10:00–10:05, so the 10:05 corner now counts.
    act(() => handler?.({ time: seconds(bars[0].ts_event) }));
    expect(chip().textContent).toContain("Goals 1");
    expect(chip().textContent).toContain("Corners 1");
  });

  it("shows n/a instead of zero when a match-state source is unavailable", () => {
    render(
      <ThemeProvider>
        <PriceChart bars={bars} lifecycle={unavailableMatchState} trades={[]} signals={[]} />
      </ThemeProvider>,
    );
    const chip = screen.getByTestId("match-state");
    expect(chip.textContent).toContain("Goals n/a");
    expect(chip.textContent).toContain("Corners n/a");
    expect(chip.textContent).not.toMatch(/Goals 0|Corners 0/);
  });

  it("adds no live-state chip when the lifecycle carries no match state", () => {
    renderChart(lifecycleGoalGap);
    expect(screen.queryByTestId("match-state")).toBeNull();
  });
});
