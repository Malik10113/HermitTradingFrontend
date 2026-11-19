import type {
  Bar,
  IndicatorInstance,
  IndicatorPreviewResponse,
  IndicatorSpec,
} from "../api/types";
import { alignIndicatorSeries } from "./chartMarkers";
import type { ChartIndicatorSeries } from "../components/PriceChart";
import { INDICATOR_PALETTE } from "./palette";

/**
 * Shape a server indicator preview into chart series. No indicator math happens
 * here: values are copied from the response, or the mismatch is surfaced.
 */
export function buildChartIndicators({
  bars,
  instances,
  preview,
  specs,
}: {
  bars: Bar[];
  instances: IndicatorInstance[];
  preview: IndicatorPreviewResponse | null;
  specs: IndicatorSpec[];
}): { series: ChartIndicatorSeries[]; mismatch: string | null } {
  const specsById = new Map(specs.map((spec) => [spec.id, spec]));
  const timestamps = preview ? (preview.timestamps ?? preview.ts_event ?? null) : null;
  let mismatch: string | null = null;
  const series: ChartIndicatorSeries[] = [];

  instances.forEach((instance, index) => {
    if (instance.visible === false) return;
    const spec = specsById.get(instance.type);
    const pane = spec?.pane === "separate" ? "separate" : "overlay";
    const base = {
      id: instance.instance_id,
      label: spec?.label ?? instance.type,
      color: instance.color ?? INDICATOR_PALETTE[index % INDICATOR_PALETTE.length],
      pane,
      warmup: preview?.warmup?.[instance.instance_id] ?? spec?.warmup ?? null,
    } as const;
    const outputSeries = preview?.series?.[instance.instance_id];
    if (!preview || !outputSeries) {
      // A separate pane is owned by the selection, not by the preview: without
      // values it stays visible and says so. An empty overlay would only add a
      // legend entry for nothing, so it is still omitted.
      if (preview && !mismatch) {
        mismatch = `no series returned for ${instance.instance_id}`;
      }
      if (pane === "separate") {
        series.push({ ...base, lines: [], status: preview ? "empty" : "loading" });
      }
      return;
    }
    const labelsByKey = new Map(
      (spec?.outputs ?? []).map((output) =>
        typeof output === "string"
          ? ([output, output] as const)
          : ([output.key, output.label ?? output.key] as const),
      ),
    );
    const lines = Object.entries(outputSeries)
      .map(([key, values]) => {
        const aligned = alignIndicatorSeries(bars, values, timestamps);
        if (aligned.mismatch && !mismatch) mismatch = aligned.mismatch;
        return { key, label: labelsByKey.get(key) ?? key, points: aligned.points };
      })
      .filter((line) => line.points.length > 0);
    if (lines.length === 0) {
      if (pane === "separate") {
        series.push({ ...base, lines: [], status: "empty" });
      }
      return;
    }
    series.push({ ...base, lines, status: "ready" });
  });

  return { series, mismatch };
}
