/** Deterministic date helpers for range buttons; all values are UTC dates. */

export function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

/** Accept a full ISO timestamp or date and return YYYY-MM-DD, else "". */
export function isoDateOnly(value: string | null | undefined): string {
  if (!value) return "";
  const candidate = value.slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(candidate) ? candidate : "";
}

export function addDaysIso(iso: string, days: number): string {
  const parsed = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime())) return iso;
  parsed.setUTCDate(parsed.getUTCDate() + days);
  return parsed.toISOString().slice(0, 10);
}

export function minIso(a: string | null | undefined, b: string | null | undefined): string {
  if (!a) return b ?? "";
  if (!b) return a;
  return a < b ? a : b;
}

export function maxIso(a: string | null | undefined, b: string | null | undefined): string {
  if (!a) return b ?? "";
  if (!b) return a;
  return a > b ? a : b;
}
