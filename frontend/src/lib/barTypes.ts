import type { BarAlias } from "../api/types";

export { EXECUTION_PROFILES } from "../api/types";

export const BAR_ALIASES: Array<{ alias: BarAlias; label: string; kind: string }> = [
  { alias: "1m", label: "1m", kind: "time" },
  { alias: "5m", label: "5m", kind: "time" },
  { alias: "15m", label: "15m", kind: "time" },
  { alias: "1h", label: "1h", kind: "time" },
  { alias: "1d", label: "1d", kind: "time" },
  { alias: "tick_100", label: "100 ticks", kind: "tick" },
  { alias: "volume_1000", label: "1,000 contracts", kind: "volume" },
  { alias: "value_10000", label: "$10,000", kind: "value" },
  { alias: "renko_0.01", label: "Renko 0.01", kind: "renko" },
];

export function barTypeLabel(alias: string): string {
  return BAR_ALIASES.find((item) => item.alias === alias)?.label ?? alias;
}

export function isTimeBar(alias: string): boolean {
  return BAR_ALIASES.find((item) => item.alias === alias)?.kind === "time";
}

/** Bar durations in milliseconds for time bars (whole-day bars included). */
export const BAR_DURATION_MS: Partial<Record<BarAlias, number>> = {
  "1m": 60_000,
  "5m": 5 * 60_000,
  "15m": 15 * 60_000,
  "1h": 60 * 60_000,
  "1d": 24 * 60 * 60_000,
};

/** Range buttons, resolved against the selected instrument's coverage. */
export const CHART_RANGES = [
  { id: "all", label: "All", days: null },
  { id: "1m", label: "1M", days: 31 },
  { id: "3m", label: "3M", days: 92 },
  { id: "1y", label: "1Y", days: 366 },
] as const;

export type ChartRangeId = (typeof CHART_RANGES)[number]["id"];
