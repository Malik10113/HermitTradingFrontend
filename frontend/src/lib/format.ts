/**
 * Formatting helpers. Every helper returns a string carrying its unit, and
 * returns "not available" for a missing or non-finite value. The UI never
 * substitutes zero for missing data.
 */

export const NOT_AVAILABLE = "not available";

export function isNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function withNotAvailable(value: unknown, format: (value: number) => string): string {
  return isNumber(value) ? format(value) : NOT_AVAILABLE;
}

export function fmtNumber(value: unknown, digits = 2): string {
  return withNotAvailable(value, (v) =>
    v.toLocaleString("en-GB", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }),
  );
}

export function fmtInteger(value: unknown): string {
  return withNotAvailable(value, (v) => Math.round(v).toLocaleString("en-GB"));
}

/** A probability in 0–1, shown to the venue's 0.001 tick precision. */
export function fmtProbability(value: unknown): string {
  return withNotAvailable(value, (v) => v.toFixed(3));
}

/** A probability change, in YES-equivalent probability points. */
export function fmtProbabilityPoints(value: unknown, digits = 3): string {
  return withNotAvailable(value, (v) => `${v >= 0 ? "+" : ""}${v.toFixed(digits)} pts`);
}

export function fmtUsd(value: unknown, options: { digits?: number; signed?: boolean } = {}): string {
  const { digits = 2, signed = false } = options;
  return withNotAvailable(value, (v) => {
    const absolute = Math.abs(v).toLocaleString("en-GB", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
    const sign = v < 0 ? "-" : signed && v > 0 ? "+" : "";
    return `${sign}$${absolute}`;
  });
}

export function fmtGbp(value: unknown, options: { digits?: number; signed?: boolean } = {}): string {
  const { digits = 2, signed = false } = options;
  return withNotAvailable(value, (v) => {
    const absolute = Math.abs(v).toLocaleString("en-GB", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
    const sign = v < 0 ? "-" : signed && v > 0 ? "+" : "";
    return `${sign}$${absolute}`;
  });
}

/** A percentage that is already expressed in percent (e.g. 12.3 → "12.3%"). */
export function fmtPct(value: unknown, digits = 1, options: { signed?: boolean } = {}): string {
  return withNotAvailable(value, (v) => {
    const sign = options.signed && v > 0 ? "+" : "";
    return `${sign}${v.toFixed(digits)}%`;
  });
}

/** A ratio in 0–1 shown as a percentage (e.g. 0.625 → "62.5%"). */
export function fmtRate(value: unknown, digits = 1): string {
  return withNotAvailable(value, (v) => `${(v * 100).toFixed(digits)}%`);
}

export function fmtBps(value: unknown, digits = 1): string {
  return withNotAvailable(value, (v) => `${v.toFixed(digits)} bps`);
}

/** A price move expressed in probability points (0.001 probability = 0.1 pts). */
export function fmtPricePoints(value: unknown, digits = 2): string {
  return withNotAvailable(value, (v) => `${v.toFixed(digits)} pts`);
}

export function fmtContracts(value: unknown): string {
  return withNotAvailable(value, (v) =>
    `${v.toLocaleString("en-GB", { maximumFractionDigits: 2 })} contracts`,
  );
}

export function fmtDuration(seconds: unknown): string {
  return withNotAvailable(seconds, (raw) => {
    const total = Math.max(0, Math.round(raw));
    if (total < 60) return `${total}s`;
    const minutes = Math.floor(total / 60);
    if (minutes < 60) return `${minutes}m ${total % 60}s`;
    const hours = Math.floor(minutes / 60);
    if (hours < 48) return `${hours}h ${minutes % 60}m`;
    const days = Math.floor(hours / 24);
    return `${days}d ${hours % 24}h`;
  });
}

/** Stable UTC rendering, independent of the browser locale or timezone. */
export function fmtTimestamp(iso: string | null | undefined, withSeconds = true): string {
  if (!iso) return NOT_AVAILABLE;
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return NOT_AVAILABLE;
  return `${parsed.toISOString().slice(0, withSeconds ? 19 : 16).replace("T", " ")} UTC`;
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return NOT_AVAILABLE;
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return NOT_AVAILABLE;
  return parsed.toISOString().slice(0, 10);
}

export function fmtVersion(version: string | null | undefined): string {
  if (!version) return NOT_AVAILABLE;
  return version.length > 16 ? `${version.slice(0, 12)}…` : version;
}

export function fmtSigned(value: number, formatter: (v: number) => string): string {
  return value >= 0 ? `+${formatter(value)}` : formatter(value);
}

/** Profitability is never asserted; a metric is only described as measured. */
export function metricTone(value: unknown): "positive" | "negative" | "neutral" {
  if (!isNumber(value) || value === 0) return "neutral";
  return value > 0 ? "positive" : "negative";
}
