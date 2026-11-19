import { createElement } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { DataPage } from "../pages/DataPage";
import { sortInstruments } from "./instrumentSort";

type Row = { league?: string | null; instrument_id: string };

function row(instrumentId: string, league?: string | null): Row {
  return { instrument_id: instrumentId, league };
}

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: "OK",
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

describe("sortInstruments", () => {
  const rows: Row[] = [
    row("id-nba", "NBA"),
    row("id-epl", "epl"),
    row("id-laliga", "LaLiga"),
    row("id-null", null),
    row("id-empty", ""),
    row("id-also-null", undefined),
  ];

  it("orders leagues case-insensitively ascending", () => {
    expect(sortInstruments(rows, "asc").map((item) => item.instrument_id)).toEqual([
      "id-epl",
      "id-laliga",
      "id-nba",
      "id-also-null",
      "id-empty",
      "id-null",
    ]);
  });

  it("reverses named leagues descending but keeps null/empty last", () => {
    expect(sortInstruments(rows, "desc").map((item) => item.instrument_id)).toEqual([
      "id-nba",
      "id-laliga",
      "id-epl",
      "id-also-null",
      "id-empty",
      "id-null",
    ]);
  });

  it("breaks ties by instrument_id ascending in both directions", () => {
    const tied: Row[] = [row("b", "epl"), row("a", "epl"), row("c", "epl")];
    expect(sortInstruments(tied, "asc").map((item) => item.instrument_id)).toEqual(["a", "b", "c"]);
    expect(sortInstruments(tied, "desc").map((item) => item.instrument_id)).toEqual(["a", "b", "c"]);

    const tiedNulls: Row[] = [row("z", null), row("y", ""), row("x", null)];
    expect(sortInstruments(tiedNulls, "asc").map((item) => item.instrument_id)).toEqual([
      "x",
      "y",
      "z",
    ]);
    expect(sortInstruments(tiedNulls, "desc").map((item) => item.instrument_id)).toEqual([
      "x",
      "y",
      "z",
    ]);
  });

  it("returns a shallow copy in input order when direction is null", () => {
    const sorted = sortInstruments(rows, null);
    expect(sorted).toEqual(rows);
    expect(sorted).not.toBe(rows);
    expect(sorted.map((item) => item.instrument_id)).toEqual([
      "id-nba",
      "id-epl",
      "id-laliga",
      "id-null",
      "id-empty",
      "id-also-null",
    ]);
  });

  it("never mutates the input array", () => {
    const original = sortInstruments(rows, null);
    sortInstruments(rows, "asc");
    sortInstruments(rows, "desc");
    expect(rows).toEqual(original);
  });

  it("handles an empty list", () => {
    expect(sortInstruments([], "asc")).toEqual([]);
    expect(sortInstruments([], null)).toEqual([]);
  });
});

describe("DataPage league sorting", () => {
  const instruments = [
    { instrument_id: "z-nba", league: "NBA", trade_count: 10 },
    { instrument_id: "a-epl", league: "epl", trade_count: 5 },
    { instrument_id: "m-unknown", league: null, trade_count: 1 },
  ];

  function leagueColumn(): string[] {
    return Array.from(document.querySelectorAll("tbody tr")).map(
      (element) => element.children[2]?.textContent?.trim() ?? "",
    );
  }

  it("cycles the League header through asc and desc over the loaded rows", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/instruments")) {
          return jsonResponse({ instruments, total: 3 });
        }
        throw new Error(`Unexpected request in test: ${url}`);
      }),
    );

    render(createElement(MemoryRouter, null, createElement(DataPage)));

    await waitFor(() => expect(leagueColumn()).toHaveLength(3));
    expect(leagueColumn()).toEqual(["NBA", "epl", "not available"]);
    expect(screen.queryByText("sorted within loaded rows")).toBeNull();

    const header = screen.getByRole("columnheader", { name: /league/i });
    expect(header).toHaveAttribute("aria-sort", "none");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /^league/i }));

    await waitFor(() => expect(header).toHaveAttribute("aria-sort", "ascending"));
    expect(leagueColumn()).toEqual(["epl", "NBA", "not available"]);
    expect(screen.getByText("sorted within loaded rows")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^league/i }));

    await waitFor(() => expect(header).toHaveAttribute("aria-sort", "descending"));
    expect(leagueColumn()).toEqual(["NBA", "epl", "not available"]);
  });
});
