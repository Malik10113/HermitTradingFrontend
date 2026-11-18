import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { normaliseRun } from "../../api/normalise";
import {
  buildExperimentRequest,
  buildHeatmap,
  buildVariantRows,
  heatmapColour,
  MAX_SWEEP_VARIANTS,
  parseAxisValues,
  sortVariantRows,
  SweepsPanel,
  variantCount,
  type VariantRow,
} from "./SweepsPanel";

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: "OK",
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

const instrumentId = "epl-bou-ars-2024-10-19-bou-YES.POLYMARKET";

const builtins = {
  builtins: [
    {
      id: "nautilus_ema_cross",
      label: "EMA cross",
      parameters: [
        { name: "fast_period", label: "Fast period", type: "int", default: 12, min: 2, max: 100, step: 1 },
        { name: "slow_period", label: "Slow period", type: "int", default: 26, min: 3, max: 200, step: 1 },
      ],
      defaults: { fast_period: 12, slow_period: 26 },
    },
  ],
  saved: [],
};

const instruments = {
  instruments: [
    {
      instrument_id: instrumentId,
      question: "Bournemouth vs Arsenal",
      selection: "YES",
      trade_count: 500,
      coverage: { from: "2024-09-01T00:00:00Z", to: "2024-12-01T00:00:00Z" },
    },
    {
      instrument_id: "epl-other-YES.POLYMARKET",
      question: "Other match",
      selection: "YES",
      trade_count: 10,
      coverage: { from: "2024-10-01T00:00:00Z", to: "2024-10-10T00:00:00Z" },
    },
  ],
};

const experiments = {
  items: [
    {
      id: "exp_1",
      name: "EMA sweep",
      status: "running",
      completed: 2,
      total: 4,
      created_at: "2024-11-01T10:00:00Z",
    },
  ],
  experiments: [
    {
      id: "exp_1",
      name: "EMA sweep",
      status: "running",
      completed: 2,
      total: 4,
      created_at: "2024-11-01T10:00:00Z",
    },
  ],
};

function childRun(runId: string, fast: number, slow: number, net: number) {
  return {
    run_id: runId,
    status: "complete",
    config: {
      strategy: {
        instrument_id: instrumentId,
        bar_type: "1m",
        builtin_id: "nautilus_ema_cross",
        builtin_params: { fast_period: fast, slow_period: slow },
      },
    },
    summary: {
      gross_pnl_usd: net + 2,
      net_pnl_usd: net,
      net_return_pct: net / 2,
      sharpe: 1.5,
      max_drawdown_pct: -3.2,
      trade_count: 4,
    },
    effective_profile: "conservative",
    requested_profile: "conservative",
  };
}

const experimentDetail = {
  id: "exp_1",
  name: "EMA sweep",
  status: "running",
  completed: 2,
  total: 4,
  created_at: "2024-11-01T10:00:00Z",
  config: {
    name: "EMA sweep",
    axes: [
      { path: "strategy.builtin_params.fast_period", values: [8, 12] },
      { path: "strategy.builtin_params.slow_period", values: [20, 30] },
    ],
    base: {},
  },
  runs: [
    childRun("run_a", 8, 20, 5),
    childRun("run_b", 12, 20, 15),
    childRun("run_c", 8, 30, -2),
    childRun("run_d", 12, 30, 9),
  ],
};

function mockApi(overrides: { onPost?: (body: unknown) => void } = {}) {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, init });
    if (url.endsWith("/api/strategies")) return jsonResponse(builtins);
    if (url.includes("/api/instruments")) return jsonResponse(instruments);
    if (url.endsWith("/api/experiments/exp_1")) return jsonResponse(experimentDetail);
    if (url.endsWith("/api/experiments") && init?.method === "POST") {
      const body = init.body ? JSON.parse(String(init.body)) : null;
      overrides.onPost?.(body);
      return jsonResponse({ experiment_id: "exp_new", run_ids: [], total: 6 }, 202);
    }
    if (url.endsWith("/api/experiments")) return jsonResponse(experiments);
    throw new Error(`Unexpected request in test: ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock };
}

function makeRow(
  fast: number,
  slow: number,
  net: number | null,
  runId = `run_${fast}_${slow}`,
): VariantRow {
  return {
    run_id: runId,
    status: "complete",
    values: {
      "strategy.builtin_params.fast_period": fast,
      "strategy.builtin_params.slow_period": slow,
    },
    gross_pnl_usd: net === null ? null : net + 2,
    net_pnl_usd: net,
    net_return_pct: net === null ? null : net / 2,
    sharpe: null,
    max_drawdown_pct: null,
    trade_count: 4,
  };
}

describe("sweep payload", () => {
  it("builds one and two axis POST bodies and de-duplicates values", () => {
    expect(parseAxisValues("8, 12, 12, nope, 16")).toEqual({
      values: [8, 12, 16],
      invalid: ["nope"],
    });

    const payload = buildExperimentRequest({
      name: "  EMA sweep ",
      builtinId: "nautilus_ema_cross",
      builtinDefaults: { fast_period: 12, slow_period: 26 },
      instrumentId,
      from: "2024-09-01",
      to: "2024-12-01",
      axes: [
        { path: "strategy.builtin_params.fast_period", values: [8, 12] },
        { path: "strategy.builtin_params.slow_period", values: [20, 30] },
      ],
    });

    expect(payload.name).toBe("EMA sweep");
    expect(payload.base.strategy).toEqual({
      instrument_id: instrumentId,
      bar_type: "1m",
      builtin_id: "nautilus_ema_cross",
      builtin_params: { fast_period: 12, slow_period: 26 },
    });
    expect(payload.base.evaluation.date_range).toEqual({ from: "2024-09-01", to: "2024-12-01" });
    expect(payload.axes).toEqual([
      { path: "strategy.builtin_params.fast_period", values: [8, 12] },
      { path: "strategy.builtin_params.slow_period", values: [20, 30] },
    ]);
  });

  it("counts variants and enforces the 50-variant UI cap", () => {
    const axes = [
      { path: "a", values: Array.from({ length: 10 }, (_, index) => index) },
      { path: "b", values: Array.from({ length: 6 }, (_, index) => index) },
    ];
    expect(variantCount(axes)).toBe(60);
    expect(variantCount(axes)).toBeGreaterThan(MAX_SWEEP_VARIANTS);
    expect(variantCount([{ path: "a", values: [] }])).toBe(0);
  });
});

describe("sweep heatmap mapping", () => {
  it("orders rows and columns by the declared axis values and keeps gaps explicit", () => {
    const rowAxis = { path: "strategy.builtin_params.fast_period", values: [8, 12] };
    const colAxis = { path: "strategy.builtin_params.slow_period", values: [20, 30] };
    const rows = [makeRow(12, 30, 9), makeRow(8, 20, 5), makeRow(12, 20, 15)];

    const heatmap = buildHeatmap(rowAxis, colAxis, rows, "net_pnl_usd");

    expect(heatmap.rows).toEqual([8, 12]);
    expect(heatmap.cols).toEqual([20, 30]);
    expect(heatmap.cells[0][0]).toMatchObject({ runId: "run_8_20", value: 5 });
    expect(heatmap.cells[0][1]).toMatchObject({ runId: null, value: null });
    expect(heatmap.cells[1][0]).toMatchObject({ runId: "run_12_20", value: 15 });
    expect(heatmap.cells[1][1]).toMatchObject({ runId: "run_12_30", value: 9 });
    expect(heatmap.min).toBe(5);
    expect(heatmap.max).toBe(15);
    expect(heatmapColour(null, 5, 15)).toBe("transparent");
    expect(heatmapColour(5, 5, 15)).toContain("rgba(86,180,233");
  });

  it("builds variant rows from stored child config and metrics", () => {
    const detailAxes = experimentDetail.config.axes;
    const rows = buildVariantRows(detailAxes, [
      normaliseRun(childRun("run_a", 8, 20, 5)),
      normaliseRun(childRun("run_b", 12, 30, 9)),
    ]);
    expect(rows[0].values).toEqual({
      "strategy.builtin_params.fast_period": 8,
      "strategy.builtin_params.slow_period": 20,
    });
    expect(rows[1]).toMatchObject({ net_pnl_usd: 9, gross_pnl_usd: 11, trade_count: 4 });
  });
});

describe("variant table sorting", () => {
  const axes = experimentDetail.config.axes;
  const rows = [makeRow(8, 20, 5), makeRow(12, 20, -2), makeRow(16, 30, null)];

  it("sorts by metric with missing values last in both directions", () => {
    const descending = sortVariantRows(rows, "net_pnl_usd", "desc", axes);
    expect(descending.map((row) => row.run_id)).toEqual(["run_8_20", "run_12_20", "run_16_30"]);

    const ascending = sortVariantRows(rows, "net_pnl_usd", "asc", axes);
    expect(ascending.map((row) => row.run_id)).toEqual(["run_12_20", "run_8_20", "run_16_30"]);
  });

  it("keeps the declared variant order for the default sort", () => {
    const sorted = sortVariantRows(rows, "variant", "asc", axes);
    expect(sorted.map((row) => row.run_id)).toEqual(["run_8_20", "run_12_20", "run_16_30"]);
  });
});

describe("SweepsPanel", () => {
  it("posts a two-axis sweep using the instrument coverage defaults", async () => {
    let posted: unknown = null;
    const { calls } = mockApi({ onPost: (body) => (posted = body) });
    const user = userEvent.setup();

    render(
      <MemoryRouter>
        <SweepsPanel />
      </MemoryRouter>,
    );

    await screen.findByRole("option", { name: /Bournemouth vs Arsenal/ });
    await user.click(screen.getByLabelText(/Add a second axis/));
    await user.clear(screen.getByLabelText("First axis values"));
    await user.type(screen.getByLabelText("First axis values"), "8, 12");
    await user.clear(screen.getByLabelText("Second axis values"));
    await user.type(screen.getByLabelText("Second axis values"), "20, 30");

    const create = screen.getByRole("button", { name: /Create sweep \(4 variants\)/ });
    await waitFor(() => expect(create).toBeEnabled());
    await user.click(create);

    await waitFor(() => expect(posted).not.toBeNull());
    expect(posted).toMatchObject({
      base: {
        strategy: { instrument_id: instrumentId, bar_type: "1m" },
        evaluation: { date_range: { from: "2024-09-01", to: "2024-12-01" } },
      },
      axes: [
        { path: "strategy.builtin_params.fast_period", values: [8, 12] },
        { path: "strategy.builtin_params.slow_period", values: [20, 30] },
      ],
    });
    expect(calls.some((call) => call.url.endsWith("/api/experiments") && call.init?.method === "POST")).toBe(
      true,
    );
  });

  it("expands an experiment, sorts the variant table and shows no winner", async () => {
    mockApi();
    const user = userEvent.setup();

    render(
      <MemoryRouter>
        <SweepsPanel />
      </MemoryRouter>,
    );

    await user.click(await screen.findByRole("button", { name: /Expand experiment EMA sweep/ }));
    const table = await screen.findByTestId("variants-table");
    const htmlTable = table as HTMLTableElement;
    expect(screen.getByText(/Weakest evidence governs/)).toBeInTheDocument();
    expect(screen.getByTestId("sweep-heatmap")).toBeInTheDocument();

    const bodyRows = () => Array.from(htmlTable.tBodies[0].rows);
    expect(bodyRows()).toHaveLength(4);

    await user.click(within(table).getByRole("button", { name: /Net P&L/ }));
    await waitFor(() => {
      expect(bodyRows()[0].textContent).toContain("$15.00");
    });
    expect(bodyRows()[3].textContent).toContain("-$2.00");

    await user.click(within(table).getByRole("button", { name: /Net P&L/ }));
    await waitFor(() => {
      expect(bodyRows()[0].textContent).toContain("-$2.00");
    });
  });
});
