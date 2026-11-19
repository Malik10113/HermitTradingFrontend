/**
 * Client-side ordering for the instrument inventory.
 *
 * The data table only holds the rows the API returned for the current page
 * (`limit: 500`), so this sorts the loaded rows and never claims to order the
 * full inventory. A row with a missing value is not invented into a bucket: it
 * sorts last in both directions, and ties break by `instrument_id` so the
 * order is deterministic. The input array is never mutated.
 */
import type { Instrument } from "../api/types";
import { instrumentLabel, tokenOf } from "../api/normalise";

export type InstrumentSortKey =
  | "instrument"
  | "venue"
  | "league"
  | "event_date"
  | "token"
  | "coverage"
  | "trade_count"
  | "grade"
  | "settlement";

export type InstrumentSortDirection = "asc" | "desc";

export interface InstrumentSort {
  key: InstrumentSortKey;
  direction: InstrumentSortDirection;
}

type SortValue = string | number | null;

/** A new key starts descending; the same key flips. */
export function nextSort(
  current: InstrumentSort | null,
  key: InstrumentSortKey,
): InstrumentSort {
  if (!current || current.key !== key) return { key, direction: "desc" };
  return { key, direction: current.direction === "desc" ? "asc" : "desc" };
}

/** Case-insensitive, trimmed text; an empty string is a missing value. */
function text(value: string | null | undefined): string | null {
  const trimmed = value?.trim();
  return trimmed ? trimmed.toLowerCase() : null;
}

/** Epoch milliseconds for an ISO-8601 timestamp; unparseable means missing. */
function epoch(value: string | null | undefined): number | null {
  const trimmed = value?.trim();
  if (!trimmed) return null;
  const parsed = Date.parse(trimmed);
  return Number.isNaN(parsed) ? null : parsed;
}

/** The grade the badge displays, so the column sorts by what the user sees. */
function displayedGrade(instrument: Instrument): string | null {
  const grade = instrument.quality_grade?.trim();
  return grade ? `grade ${grade.toUpperCase()}` : null;
}

function sortValue(
  instrument: Instrument,
  key: Exclude<InstrumentSortKey, "coverage">,
): SortValue {
  switch (key) {
    case "instrument":
      return text(instrumentLabel(instrument));
    case "venue":
      return text(instrument.venue);
    case "league":
      return text(instrument.league);
    case "event_date":
      return epoch(instrument.event_date);
    case "token":
      return text(tokenOf(instrument));
    case "trade_count":
      return typeof instrument.trade_count === "number" &&
        Number.isFinite(instrument.trade_count)
        ? instrument.trade_count
        : null;
    case "grade":
      return text(displayedGrade(instrument));
    case "settlement":
      return instrument.settlement_value === 0 || instrument.settlement_value === 1
        ? instrument.settlement_value
        : null;
  }
}

/**
 * Order two values. A missing value always sorts last, whichever direction is
 * active, so a named value never hides behind an unknown one.
 */
function compareValues(
  left: SortValue,
  right: SortValue,
  direction: InstrumentSortDirection,
): number {
  if (left === null && right === null) return 0;
  if (left === null) return 1;
  if (right === null) return -1;
  let comparison: number;
  if (typeof left === "number" && typeof right === "number") {
    comparison = left - right;
  } else {
    const a = String(left);
    const b = String(right);
    comparison = a < b ? -1 : a > b ? 1 : 0;
  }
  return direction === "asc" ? comparison : -comparison;
}

function compareCoverage(
  a: Instrument,
  b: Instrument,
  direction: InstrumentSortDirection,
): number {
  const byFrom = compareValues(epoch(a.coverage?.from), epoch(b.coverage?.from), direction);
  if (byFrom !== 0) return byFrom;
  return compareValues(epoch(a.coverage?.to), epoch(b.coverage?.to), direction);
}

/** Code-unit order: deterministic across environments, unlike a locale sort. */
function compareInstrumentId(a: Instrument, b: Instrument): number {
  if (a.instrument_id < b.instrument_id) return -1;
  if (a.instrument_id > b.instrument_id) return 1;
  return 0;
}

/**
 * Return a new array ordered by `sort`, or a shallow copy in the input order
 * when `sort` is null. The input array is never mutated.
 */
export function sortInstruments<T extends Instrument>(
  rows: readonly T[],
  sort: InstrumentSort | null,
): T[] {
  const copy = [...rows];
  if (sort === null) return copy;
  const { key, direction } = sort;
  return copy.sort((a, b) => {
    const primary =
      key === "coverage"
        ? compareCoverage(a, b, direction)
        : compareValues(sortValue(a, key), sortValue(b, key), direction);
    // Ties break the same way in both directions so the order is deterministic.
    return primary !== 0 ? primary : compareInstrumentId(a, b);
  });
}
