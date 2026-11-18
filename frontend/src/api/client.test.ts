import { describe, expect, it, vi } from "vitest";
import type { IndicatorSpec } from "../api/types";
import { api, ApiError, indicatorList } from "../api/client";

function response(
  status: number,
  body: unknown,
  statusText = "",
): Response {
  const text = typeof body === "string" ? body : JSON.stringify(body);
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText,
    text: async () => text,
  } as unknown as Response;
}

function mockFetch(result: Response | Promise<Response>) {
  const fetchMock = vi.fn().mockResolvedValue(result);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("api client", () => {
  it("builds typed bars requests and drops empty parameters", async () => {
    const fetchMock = mockFetch(
      response(200, {
        instrument_id: "x-YES.POLYMARKET",
        bar_type: "...-1-MINUTE-LAST-EXTERNAL",
        bars: [],
      }),
    );
    await api.bars({
      instrument_id: "x-YES.POLYMARKET",
      bar_type: "1m",
      from: "2024-10-01",
      to: undefined,
      limit: 500,
    });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(
      "/api/bars?instrument_id=x-YES.POLYMARKET&bar_type=1m&from=2024-10-01&limit=500",
    );
    expect(init.method).toBe("GET");
    expect(init.body).toBeUndefined();
  });

  it("encodes instrument ids in path segments", async () => {
    const fetchMock = mockFetch(response(200, { instrument_id: "a/b" }));
    await api.instrument("a/b");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/instruments/a%2Fb");
  });

  it("parses the stable {code,message,details} error shape", async () => {
    mockFetch(
      response(422, {
        code: "invalid_parameter",
        message: "period must be at least 2",
        details: { path: "strategy.indicators[0].params.period" },
      }),
    );
    await expect(api.previewIndicator({
      instrument_id: "x",
      bar_type: "1m",
      indicators: [],
    })).rejects.toMatchObject({
      name: "ApiError",
      code: "invalid_parameter",
      status: 422,
      details: { path: "strategy.indicators[0].params.period" },
    });
  });

  it("falls back to a stable http_<status> code for a plain-text error", async () => {
    mockFetch(response(503, "backend warming up", "Service Unavailable"));
    const error = await api.meta().catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).code).toBe("http_503");
    expect((error as ApiError).message).toBe("backend warming up");
  });

  it("reports an unreachable API as network_error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(api.meta()).rejects.toMatchObject({ code: "network_error", status: 0 });
  });

  it("rejects a successful response that is not JSON", async () => {
    mockFetch(response(200, "<html>proxy error</html>"));
    await expect(api.meta()).rejects.toMatchObject({ code: "invalid_json", status: 200 });
  });

  it("accepts 204 No Content without parsing", async () => {
    mockFetch(response(204, ""));
    await expect(api.deleteRun("run-1")).resolves.toBeUndefined();
  });

  it("normalises a strategies array into builtins and saved rules", async () => {
    mockFetch(
      response(200, [
        { id: "sma_trend", label: "SMA trend" },
        { id: "saved-1", label: "Saved", strategy: { instrument_id: "x", bar_type: "1m" } },
      ]),
    );
    const result = await api.strategies();
    expect(result.builtins.map((item) => item.id)).toEqual(["sma_trend"]);
    expect(result.saved.map((item) => item.id)).toEqual(["saved-1"]);
  });

  it("normalises the object shape and missing saved list", async () => {
    mockFetch(response(200, { builtins: [{ id: "b", label: "B" }] }));
    const result = await api.strategies();
    expect(result.builtins).toHaveLength(1);
    expect(result.saved).toEqual([]);
  });

  it("exposes an SSE events url", () => {
    expect(api.eventsUrl("run 1")).toBe("/api/backtests/run%201/events");
  });

  it("unwraps indicator registry responses in both shapes", () => {
    const specs: IndicatorSpec[] = [{ id: "ema", label: "EMA", pane: "overlay" }];
    expect(indicatorList(specs)).toHaveLength(1);
    expect(indicatorList({ indicators: specs })).toHaveLength(1);
    expect(indicatorList(undefined)).toEqual([]);
  });
});
