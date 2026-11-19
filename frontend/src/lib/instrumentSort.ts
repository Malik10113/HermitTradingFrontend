/**
 * Client-side ordering for the instrument inventory.
 *
 * The data table only holds the rows the API returned for the current page
 * (`limit: 500`), so this sorts the loaded rows and never claims to order the
 * full inventory. Rows with a missing league are not invented into a bucket;
 * they sort last in both directions so a named league always leads.
 */

export type InstrumentSortDirection = "asc" | "desc";

type SortableInstrument = { league?: string | null; instrument_id: string };

function leagueKey(row: SortableInstrument): string | null {
  const value = row.league?.trim();
  return value ? value : null;
}

/**
 * Order two rows by league. Returns a negative/positive number for a
 * direction-dependent difference, `0` when the leagues tie, and always puts a
 * null/empty league after a named one regardless of direction.
 */
function compareLeague(
  a: SortableInstrument,
  b: SortableInstrument,
  direction: InstrumentSortDirection,
): number {
  const left = leagueKey(a);
  const right = leagueKey(b);
  if (left === null && right === null) return 0;
  if (left === null) return 1;
  if (right === null) return -1;
  const comparison = left.localeCompare(right, undefined, { sensitivity: "base" });
  return direction === "asc" ? comparison : -comparison;
}

/** Code-unit order: deterministic across environments, unlike a locale sort. */
function compareInstrumentId(a: SortableInstrument, b: SortableInstrument): number {
  if (a.instrument_id < b.instrument_id) return -1;
  if (a.instrument_id > b.instrument_id) return 1;
  return 0;
}

/**
 * Return a new array ordered by league, or a shallow copy in the input order
 * when `direction` is null. The input array is never mutated.
 */
export function sortInstruments<T extends SortableInstrument>(
  rows: readonly T[],
  direction: InstrumentSortDirection | null,
): T[] {
  const copy = [...rows];
  if (direction === null) return copy;
  return copy.sort((a, b) => {
    const byLeague = compareLeague(a, b, direction);
    // Ties break the same way in both directions so the order is deterministic.
    return byLeague !== 0 ? byLeague : compareInstrumentId(a, b);
  });
}
