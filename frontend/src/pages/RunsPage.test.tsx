import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { RunsPage } from "./RunsPage";

// The sweep panel owns its own requests and is not the subject of this test.
vi.mock("../components/results/SweepsPanel", () => ({
  SweepsPanel: () => null,
}));

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 204 ? "No Content" : "OK",
    text: async () => (status === 204 ? "" : JSON.stringify(body)),
  } as unknown as Response;
}

function stubRunApi(status: "running" | "complete") {
  const deletes: string[] = [];
  let listCalls = 0;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (url.includes("/api/backtests/") && method === "DELETE") {
      deletes.push(url);
      return jsonResponse(null, 204);
    }
    if (url.includes("/api/backtests") && method === "GET") {
      listCalls += 1;
      return jsonResponse({
        runs: [
          {
            run_id: "run-abcdef123456",
            status,
            created_at: "2024-11-01T10:00:00Z",
            requested_profile: "conservative",
            effective_profile: "conservative",
            metrics: { net_pnl_usd: 12.5, trade_count: 4 },
          },
        ],
        total: 1,
      });
    }
    throw new Error(`Unexpected request in test: ${url} ${method}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { deletes, listCalls: () => listCalls };
}

describe("RunsPage cancel and delete", () => {
  it("cancels a running run, removes it from the selection and reloads the list", async () => {
    const calls = stubRunApi("running");
    const user = userEvent.setup();

    render(
      <MemoryRouter>
        <RunsPage />
      </MemoryRouter>,
    );

    const cancel = await screen.findByRole("button", { name: "Cancel run run-abcd…" });
    expect(cancel).toHaveTextContent("Cancel");
    expect(calls.listCalls()).toBe(1);

    await user.click(cancel);

    await waitFor(() => expect(calls.deletes).toEqual(["/api/backtests/run-abcdef123456"]));
    await waitFor(() => expect(calls.listCalls()).toBe(2));
    // The row stays put until the refreshed list arrives; nothing is fabricated.
    expect(await screen.findByRole("button", { name: "Cancel run run-abcd…" })).toBeEnabled();
  });

  it("labels a terminal run Delete and calls the same DELETE endpoint", async () => {
    const calls = stubRunApi("complete");
    const user = userEvent.setup();

    render(
      <MemoryRouter>
        <RunsPage />
      </MemoryRouter>,
    );

    const remove = await screen.findByRole("button", { name: "Delete run run-abcd…" });
    expect(remove).toHaveTextContent("Delete");
    await user.click(remove);

    await waitFor(() => expect(calls.deletes).toEqual(["/api/backtests/run-abcdef123456"]));
    await waitFor(() => expect(calls.listCalls()).toBe(2));
  });
});
