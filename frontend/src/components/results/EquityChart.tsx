import { useMemo } from "react";
import type { EquityPoint } from "../../api/types";
import { normaliseEquityCurves } from "../../lib/chartMarkers";
import { fmtDate, fmtNumber, fmtTimestamp, fmtUsd, NOT_AVAILABLE } from "../../lib/format";

interface SvgPoint {
  time: number;
  value: number;
}

interface SvgSeries {
  label: string;
  color: string;
  points: SvgPoint[];
}

function scalePoints(series: SvgSeries[], width: number, height: number, padding: number) {
  const all = series.flatMap((item) => item.points);
  if (all.length === 0) return { rendered: [], min: 0, max: 0 };
  const times = all.map((point) => point.time);
  const values = all.map((point) => point.value);
  const minTime = Math.min(...times);
  const maxTime = Math.max(...times);
  let minValue = Math.min(...values);
  let maxValue = Math.max(...values);
  if (minValue === maxValue) {
    minValue -= 1;
    maxValue += 1;
  }
  const span = maxValue - minValue;
  minValue -= span * 0.06;
  maxValue += span * 0.06;
  const innerWidth = width - padding * 2;
  const innerHeight = height - padding * 2;
  const x = (time: number) =>
    padding + (maxTime === minTime ? innerWidth / 2 : ((time - minTime) / (maxTime - minTime)) * innerWidth);
  const y = (value: number) =>
    padding + innerHeight - ((value - minValue) / (maxValue - minValue)) * innerHeight;
  return {
    rendered: series.map((item) => ({
      ...item,
      path: item.points
        .map((point, index) => `${index === 0 ? "M" : "L"}${x(point.time).toFixed(2)},${y(point.value).toFixed(2)}`)
        .join(" "),
    })),
    min: minValue,
    max: maxValue,
    minTime,
    maxTime,
  };
}

function SvgSeriesChart({
  series,
  width = 640,
  height = 160,
  formatValue,
  ariaLabel,
  zeroLine = false,
}: {
  series: SvgSeries[];
  width?: number;
  height?: number;
  formatValue: (value: number) => string;
  ariaLabel: string;
  zeroLine?: boolean;
}) {
  const padding = 56;
  const scaled = useMemo(() => scalePoints(series, width, height, padding), [series, width, height]);
  const gridValues = useMemo(() => {
    const values: number[] = [];
    for (let index = 0; index <= 3; index += 1) {
      values.push(scaled.min + ((scaled.max - scaled.min) * index) / 3);
    }
    return values;
  }, [scaled.min, scaled.max]);
  const yFor = (value: number) =>
    padding +
    (height - padding * 2) -
    ((value - scaled.min) / (scaled.max - scaled.min || 1)) * (height - padding * 2);

  return (
    <div className="w-full overflow-hidden">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="h-auto w-full"
        role="img"
        aria-label={`${ariaLabel}. ${
          series[0]?.points.length ?? 0
        } observations; first ${series[0]?.points[0]?.time ? fmtTimestamp(new Date(series[0].points[0].time * 1000).toISOString()) : NOT_AVAILABLE}; last ${
          series[0]?.points.length
            ? fmtTimestamp(
                new Date(series[0].points[series[0].points.length - 1].time * 1000).toISOString(),
              )
            : NOT_AVAILABLE
        }.`}
      >
        {gridValues.map((value, index) => (
          <g key={index}>
            <line
              x1={padding}
              x2={width - padding}
              y1={yFor(value)}
              y2={yFor(value)}
              stroke="var(--line)"
              strokeWidth={1}
            />
            <text
              x={padding - 4}
              y={yFor(value) + 3}
              textAnchor="end"
              fontSize={8}
              fill="var(--ink-faint)"
              className="numeric"
            >
              {formatValue(value)}
            </text>
          </g>
        ))}
        {zeroLine && scaled.min < 0 && scaled.max > 0 ? (
          <line
            x1={padding}
            x2={width - padding}
            y1={yFor(0)}
            y2={yFor(0)}
            stroke="var(--line-strong)"
            strokeDasharray="3 3"
            strokeWidth={1}
          />
        ) : null}
        {scaled.rendered.map((item) => (
          <path
            key={item.label}
            d={item.path}
            fill="none"
            stroke={item.color}
            strokeWidth={1.6}
            strokeLinejoin="round"
            strokeLinecap="round"
          />
        ))}
      </svg>
      {series.length > 1 ? (
        <ul className="flex flex-wrap gap-x-3 gap-y-1 px-1 pb-1 text-[10px] text-ink-muted">
          {series.map((item) => (
            <li key={item.label} className="inline-flex items-center gap-1">
              <span
                aria-hidden="true"
                className="inline-block h-1.5 w-3 rounded-sm"
                style={{ background: item.color }}
              />
              {item.label}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function toSeries(points: EquityPoint[] | null | undefined): SvgPoint[] {
  if (!points) return [];
  return points
    .map((point) => ({
      time: Math.floor(Date.parse(point.ts) / 1000),
      value: point.equity_usd,
    }))
    .filter((point) => Number.isFinite(point.time) && Number.isFinite(point.value))
    .sort((a, b) => a.time - b.time);
}

export function EquityChart({
  equity,
  drawdown,
  profileLabelText,
}: {
  equity: EquityPoint[] | null | undefined;
  drawdown?: Array<{ ts: string; drawdown_pct: number }> | null;
  profileLabelText: string;
}) {
  const equityPoints = useMemo(() => toSeries(equity), [equity]);
  const drawdownPoints = useMemo(() => {
    const source =
      drawdown ??
      (equity ?? [])
        .filter((point) => typeof point.drawdown_pct === "number")
        .map((point) => ({ ts: point.ts, drawdown_pct: point.drawdown_pct as number }));
    return source
      .map((point) => ({
        time: Math.floor(Date.parse(point.ts) / 1000),
        value: point.drawdown_pct,
      }))
      .filter((point) => Number.isFinite(point.time) && Number.isFinite(point.value))
      .sort((a, b) => a.time - b.time);
  }, [equity, drawdown]);

  if (equityPoints.length < 2) {
    return (
      <p className="px-2 py-4 text-[11px] text-ink-muted">
        Equity curve not available — no equity artifact for this run.
      </p>
    );
  }

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between px-2 pt-1">
        <span className="label">Equity curve (USD) · {profileLabelText}</span>
        <span className="numeric text-[10px] text-ink-faint">
          {fmtTimestamp(new Date(equityPoints[0].time * 1000).toISOString(), false)} →{" "}
          {fmtTimestamp(new Date(equityPoints[equityPoints.length - 1].time * 1000).toISOString(), false)}
        </span>
      </div>
      <SvgSeriesChart
        series={[{ label: "equity", color: "#009E73", points: equityPoints }]}
        formatValue={(value) => fmtUsd(value, { digits: 0 })}
        ariaLabel="Equity curve in USD"
        zeroLine
      />
      {drawdownPoints.length > 1 ? (
        <>
          <div className="px-2 pt-1">
            <span className="label">Drawdown (%)</span>
          </div>
          <SvgSeriesChart
            series={[{ label: "drawdown", color: "#D55E00", points: drawdownPoints }]}
            height={110}
            formatValue={(value) => `${value.toFixed(1)}%`}
            ariaLabel="Drawdown in percent"
          />
        </>
      ) : (
        <p className="px-2 text-[10px] text-ink-faint">Drawdown series not available.</p>
      )}
    </div>
  );
}

export function CompareEquityChart({
  runs,
}: {
  runs: Array<{ label: string; equity: EquityPoint[] | null | undefined }>;
}) {
  const colours = ["#56B4E9", "#E69F00", "#CC79A7"];
  const normalised = useMemo(
    () =>
      normaliseEquityCurves(
        runs.map((run, index) => ({
          label: run.label,
          color: colours[index % colours.length],
          equity: (run.equity ?? []).map((point) => ({
            ts: point.ts,
            equity_usd: point.equity_usd,
          })),
        })),
      ),
    [runs],
  );
  const series: SvgSeries[] = normalised.map((item) => ({
    label: item.label,
    color: item.color,
    points: item.points,
  }));
  if (series.every((item) => item.points.length < 2)) {
    return (
      <p className="px-2 py-3 text-[11px] text-ink-muted">
        No comparable equity curves. Each run needs at least two observations inside the common
        window.
      </p>
    );
  }
  return (
    <div>
      <div className="flex items-center justify-between px-2 pt-1">
        <span className="label">Equity overlay — normalised to 100 at the common start</span>
        <span className="text-[10px] text-ink-faint">
          {normalised.length} run(s) · {series[0]?.points.length ?? 0} points
        </span>
      </div>
      <SvgSeriesChart
        series={series}
        width={640}
        height={170}
        formatValue={(value) => fmtNumber(value, 1)}
        ariaLabel="Normalised equity comparison indexed to 100"
        zeroLine
      />
      <p className="px-2 text-[10px] text-ink-faint">
        Index values are not returns and imply no profitability. Compare profiles at the same
        strategy hash to read cost differences.
      </p>
    </div>
  );
}

export function DrawdownSummary({
  equity,
}: {
  equity: EquityPoint[] | null | undefined;
}) {
  if (!equity || equity.length === 0) {
    return <span className="text-ink-faint">not available</span>;
  }
  return <span>{fmtDate(equity[equity.length - 1].ts)}</span>;
}
