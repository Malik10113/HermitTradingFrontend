import { describe, expect, it } from "vitest";
import type { Instrument } from "../api/types";
import { nextSort, sortInstruments } from "./instrumentSort";

function row(
  instrumentId: string,
  fields: Partial<Instrument> = {},
): Instrument {
  return { instrument_id: instrumentId, ...fields };
}

function ids(rows: Instrument[]): string[] {
  return rows.map((item) => item.instrument_id);
}

describe("nextSort", () => {
  it("starts a new key descending", () => {
    expect(nextSort(null, "league")).toEqual({ key: "league", direction: "desc" });
    expect(nextSort({ key: "league", direction: "asc" }, "venue")).toEqual({
      key: "venue",
      direction: "desc",
    });
    expect(nextSort({ key: "league", direction: "desc" }, "trade_count")).toEqual({
      key: "trade_count",
      direction: "desc",
    });
  });

  it("flips the active key desc → asc → desc", () => {
    const first = nextSort({ key: "league", direction: "desc" }, "league");
    expect(first).toEqual({ key: "league", direction: "asc" });
    expect(nextSort(first, "league")).toEqual({ key: "league", direction: "desc" });
  });
});

describe("sortInstruments", () => {
  it("returns a shallow copy in input order when sort is null", () => {
    const rows = [row("b", { league: "NBA" }), row("a", { league: "epl" })];
    const sorted = sortInstruments(rows, null);
    expect(sorted).toEqual(rows);
    expect(sorted).not.toBe(rows);
    expect(ids(sorted)).toEqual(["b", "a"]);
  });

  it("never mutates the input array", () => {
    const rows = [row("b", { league: "NBA" }), row("a", { league: "epl" })];
    const before = rows.map((item) => item.instrument_id);
    sortInstruments(rows, { key: "league", direction: "desc" });
    sortInstruments(rows, { key: "league", direction: "asc" });
    expect(ids(rows)).toEqual(before);
  });

  it("orders leagues case-insensitively with missing leagues last", () => {
    const rows = [
      row("id-nba", { league: "NBA" }),
      row("id-epl", { league: "epl" }),
      row("id-laliga", { league: "LaLiga" }),
      row("id-null", { league: null }),
      row("id-empty", { league: "" }),
      row("id-undefined", {}),
    ];
    expect(ids(sortInstruments(rows, { key: "league", direction: "asc" }))).toEqual([
      "id-epl",
      "id-laliga",
      "id-nba",
      "id-empty",
      "id-null",
      "id-undefined",
    ]);
    expect(ids(sortInstruments(rows, { key: "league", direction: "desc" }))).toEqual([
      "id-nba",
      "id-laliga",
      "id-epl",
      "id-empty",
      "id-null",
      "id-undefined",
    ]);
  });

  it("orders venues case-insensitively with missing venues last", () => {
    const rows = [
      row("b", { venue: "Polymarket" }),
      row("c", { venue: null }),
      row("a", { venue: "betfair" }),
    ];
    expect(ids(sortInstruments(rows, { key: "venue", direction: "asc" }))).toEqual([
      "a",
      "b",
      "c",
    ]);
    expect(ids(sortInstruments(rows, { key: "venue", direction: "desc" }))).toEqual([
      "b",
      "a",
      "c",
    ]);
  });

  it("orders instruments by the displayed label", () => {
    const rows = [
      row("id-b", { home_name: "Beta", away_name: "Zulu" }),
      row("id-a", { question: "alpha market" }),
      row("id-c", { question: "Gamma market" }),
    ];
    expect(ids(sortInstruments(rows, { key: "instrument", direction: "asc" }))).toEqual([
      "id-a",
      "id-b",
      "id-c",
    ]);
  });

  it("orders event dates chronologically, not lexicographically", () => {
    const rows = [
      row("id-late", { event_date: "2026-01-09T23:00:00Z" }),
      row("id-early", { event_date: "2026-01-10T00:00:00+02:00" }),
      row("id-missing", { event_date: null }),
      row("id-invalid", { event_date: "not a date" }),
    ];
    expect(ids(sortInstruments(rows, { key: "event_date", direction: "asc" }))).toEqual([
      "id-early",
      "id-late",
      "id-invalid",
      "id-missing",
    ]);
    expect(ids(sortInstruments(rows, { key: "event_date", direction: "desc" }))).toEqual([
      "id-late",
      "id-early",
      "id-invalid",
      "id-missing",
    ]);
  });

  it("orders tokens by YES/NO", () => {
    const rows = [
      row("id-yes", { token_index: 0, selection: "YES" }),
      row("id-no", { token_index: 1, selection: "NO" }),
      row("id-unknown", {}),
    ];
    expect(ids(sortInstruments(rows, { key: "token", direction: "asc" }))).toEqual([
      "id-no",
      "id-yes",
      "id-unknown",
    ]);
    expect(ids(sortInstruments(rows, { key: "token", direction: "desc" }))).toEqual([
      "id-yes",
      "id-no",
      "id-unknown",
    ]);
  });

  it("orders coverage by from then to with missing coverage last", () => {
    const rows = [
      row("id-b", { coverage: { from: "2026-02-01T00:00:00Z", to: "2026-03-01T00:00:00Z" } }),
      row("id-c", { coverage: { from: "2026-02-01T00:00:00Z", to: "2026-04-01T00:00:00Z" } }),
      row("id-a", { coverage: { from: "2026-01-01T00:00:00Z", to: "2026-05-01T00:00:00Z" } }),
      row("id-none", { coverage: null }),
    ];
    expect(ids(sortInstruments(rows, { key: "coverage", direction: "asc" }))).toEqual([
      "id-a",
      "id-b",
      "id-c",
      "id-none",
    ]);
    expect(ids(sortInstruments(rows, { key: "coverage", direction: "desc" }))).toEqual([
      "id-c",
      "id-b",
      "id-a",
      "id-none",
    ]);
  });

  it("orders fills numerically with missing counts last", () => {
    const rows = [
      row("id-five", { trade_count: 5 }),
      row("id-ten", { trade_count: 10 }),
      row("id-one", { trade_count: 1 }),
      row("id-zero", { trade_count: 0 }),
      row("id-missing", { trade_count: null }),
    ];
    expect(ids(sortInstruments(rows, { key: "trade_count", direction: "asc" }))).toEqual([
      "id-zero",
      "id-one",
      "id-five",
      "id-ten",
      "id-missing",
    ]);
    expect(ids(sortInstruments(rows, { key: "trade_count", direction: "desc" }))).toEqual([
      "id-ten",
      "id-five",
      "id-one",
      "id-zero",
      "id-missing",
    ]);
  });

  it("orders grades by the displayed badge text with missing grades last", () => {
    const rows = [
      row("id-b", { quality_grade: "B" }),
      row("id-a", { quality_grade: "a" }),
      row("id-c", { quality_grade: "C" }),
      row("id-none", { quality_grade: null }),
    ];
    expect(ids(sortInstruments(rows, { key: "grade", direction: "asc" }))).toEqual([
      "id-a",
      "id-b",
      "id-c",
      "id-none",
    ]);
    expect(ids(sortInstruments(rows, { key: "grade", direction: "desc" }))).toEqual([
      "id-c",
      "id-b",
      "id-a",
      "id-none",
    ]);
  });

  it("orders settlement numerically with unknown settlement last", () => {
    const rows = [
      row("id-yes", { settlement_value: 1 }),
      row("id-no", { settlement_value: 0 }),
      row("id-unknown", { settlement_value: null }),
    ];
    expect(ids(sortInstruments(rows, { key: "settlement", direction: "asc" }))).toEqual([
      "id-no",
      "id-yes",
      "id-unknown",
    ]);
    expect(ids(sortInstruments(rows, { key: "settlement", direction: "desc" }))).toEqual([
      "id-yes",
      "id-no",
      "id-unknown",
    ]);
  });

  it("breaks ties by instrument_id ascending in both directions", () => {
    const rows = [
      row("b", { league: "epl" }),
      row("a", { league: "epl" }),
      row("c", { league: "epl" }),
    ];
    expect(ids(sortInstruments(rows, { key: "league", direction: "asc" }))).toEqual([
      "a",
      "b",
      "c",
    ]);
    expect(ids(sortInstruments(rows, { key: "league", direction: "desc" }))).toEqual([
      "a",
      "b",
      "c",
    ]);

    const missing = [row("z"), row("y", { trade_count: null }), row("x")];
    expect(ids(sortInstruments(missing, { key: "trade_count", direction: "desc" }))).toEqual([
      "x",
      "y",
      "z",
    ]);
  });

  it("handles an empty list", () => {
    expect(sortInstruments([], { key: "league", direction: "asc" })).toEqual([]);
    expect(sortInstruments([], null)).toEqual([]);
  });
});
