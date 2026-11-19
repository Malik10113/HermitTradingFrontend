import { createElement } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { DataPage } from "./DataPage";

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: "OK",
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

const instruments = [
  { instrument_id: "z-nba", league: "NBA", trade_count: 5 },
  { instrument_id: "a-epl", league: "epl", trade_count: 10 },
  { instrument_id: "m-unknown", league: null, trade_count: 1 },
];

function stubInstruments() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/api/instruments")) {
      return jsonResponse({ instruments, total: instruments.length });
    }
    throw new Error(`Unexpected request in test: ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

type FetchMock = ReturnType<typeof stubInstruments>;

function instrumentUrls(fetchMock: FetchMock): string[] {
  return fetchMock.mock.calls
    .map((call) => String(call[0]))
    .filter((url) => url.includes("/api/instruments"));
}

function lastInstrumentUrl(fetchMock: FetchMock): string {
  const urls = instrumentUrls(fetchMock);
  return urls[urls.length - 1] ?? "";
}

function column(index: number): string[] {
  return Array.from(document.querySelectorAll("tbody tr")).map(
    (element) => element.children[index]?.textContent?.trim() ?? "",
  );
}

async function renderPage(initialEntries: string[] = ["/"]) {
  const fetchMock = stubInstruments();
  render(
    createElement(
      MemoryRouter,
      { initialEntries },
      createElement(DataPage),
    ),
  );
  await waitFor(() => expect(column(2)).toHaveLength(instruments.length));
  return fetchMock;
}

describe("DataPage column sorting", () => {
  it("sorts by League descending on the first click and ascending on the second", async () => {
    await renderPage();

    expect(column(2)).toEqual(["NBA", "epl", "not available"]);
    expect(screen.queryByText("sorted within loaded rows")).toBeNull();

    const header = screen.getByRole("columnheader", { name: /league/i });
    expect(header).toHaveAttribute("aria-sort", "none");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /^league/i }));

    await waitFor(() => expect(header).toHaveAttribute("aria-sort", "descending"));
    expect(column(2)).toEqual(["NBA", "epl", "not available"]);
    expect(screen.getByText("sorted within loaded rows")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^league/i }));

    await waitFor(() => expect(header).toHaveAttribute("aria-sort", "ascending"));
    expect(column(2)).toEqual(["epl", "NBA", "not available"]);
  });

  it("starts a different column at descending", async () => {
    await renderPage();
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: /^league/i }));
    await waitFor(() =>
      expect(screen.getByRole("columnheader", { name: /league/i })).toHaveAttribute(
        "aria-sort",
        "descending",
      ),
    );

    await user.click(screen.getByRole("button", { name: /^fills/i }));

    await waitFor(() =>
      expect(screen.getByRole("columnheader", { name: /fills/i })).toHaveAttribute(
        "aria-sort",
        "descending",
      ),
    );
    expect(screen.getByRole("columnheader", { name: /league/i })).toHaveAttribute(
      "aria-sort",
      "none",
    );
    // Fills render through fmtInteger, so compare the numeric order not the text.
    expect(column(6)).toEqual(["10", "5", "1"]);
  });
});

describe("DataPage page size", () => {
  it("requests the default page size of 500", async () => {
    const fetchMock = await renderPage();

    expect(lastInstrumentUrl(fetchMock)).toContain("limit=500");
    expect(screen.getByLabelText("Markets loaded")).toHaveValue("500");
  });

  it("re-queries with the chosen size without clearing filters or the sort", async () => {
    const fetchMock = await renderPage(["/?league=NBA"]);
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: /^league/i }));
    await waitFor(() =>
      expect(screen.getByRole("columnheader", { name: /league/i })).toHaveAttribute(
        "aria-sort",
        "descending",
      ),
    );

    const select = screen.getByLabelText("Markets loaded");
    await user.selectOptions(select, "1000");

    await waitFor(() => expect(lastInstrumentUrl(fetchMock)).toContain("limit=1000"));
    expect(select).toHaveValue("1000");
    // The other URL filters and the client-side sort survive the new request.
    expect(lastInstrumentUrl(fetchMock)).toContain("league=NBA");
    expect(column(2)).toEqual(["NBA", "epl", "not available"]);
    expect(screen.getByRole("columnheader", { name: /league/i })).toHaveAttribute(
      "aria-sort",
      "descending",
    );
    expect(screen.getByText("sorted within loaded rows")).toBeInTheDocument();
  });

  it("falls back to 500 when the URL carries an unsupported limit", async () => {
    const fetchMock = await renderPage(["/?limit=42"]);

    expect(lastInstrumentUrl(fetchMock)).toContain("limit=500");
    expect(lastInstrumentUrl(fetchMock)).not.toContain("limit=42");
    expect(screen.getByLabelText("Markets loaded")).toHaveValue("500");
  });
});
