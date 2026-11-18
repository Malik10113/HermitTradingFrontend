import { expect, test, type Page } from "@playwright/test";

/**
 * Workspace acceptance path against the built app and real stored data.
 *
 * The suite is offline with respect to the internet but needs the local app
 * (loopback) and the `var/` fixture. It skips with a reason when the API is not
 * reachable, never silently passing.
 */

const INSTRUMENT = "epl-bri-ars-2026-09-19-ars-YES.POLYMARKET";
const PRE_2026_INSTRUMENT = "epl-ars-ast-2025-01-18-ast-YES.POLYMARKET";

function workspaceUrl(params: Record<string, string>): string {
  const query = new URLSearchParams(params);
  return `/?${query.toString()}`;
}

const chartUrl = workspaceUrl({
  instrument: INSTRUMENT,
  from: "2026-09-19",
  to: "2026-09-19",
  bar: "1m",
});

async function requireApp(page: Page): Promise<void> {
  const health = await page.request.get("/api/health").catch(() => null);
  test.skip(!health || !health.ok(), "HTF_E2E app is not running; start uvicorn on the configured base URL");
}

function collectConsoleErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(String(error)));
  return errors;
}

async function waitForChart(page: Page): Promise<void> {
  await expect(page.locator('[aria-label^="Price chart with"]')).toBeVisible({ timeout: 60_000 });
  await expect(page.locator("canvas").first()).toBeVisible();
}

async function runBacktest(page: Page): Promise<void> {
  const runButton = page.getByRole("button", { name: "Run backtest" });
  await expect(runButton).toBeEnabled();
  await runButton.click();
  const drawer = page.getByLabel("Backtest results");
  await expect(drawer).toBeVisible();
  await expect(drawer.getByText("complete", { exact: true })).toBeVisible({ timeout: 120_000 });
}

test.describe("research workspace", () => {
  test.beforeEach(async ({ page }) => {
    await requireApp(page);
  });

  test("cold start selects a liquid instrument and renders candles", async ({ page }) => {
    const errors = collectConsoleErrors(page);
    await page.addInitScript(() => window.localStorage.clear());
    await page.goto("/");
    await waitForChart(page);
    await expect(page.getByText(/bars · .* fills · first/)).toBeVisible();
    // The auto-selection must be a real instrument, not a placeholder.
    await expect(page.locator('[aria-label="Chart timeframe"]')).toHaveValue("1m");
    expect(errors).toEqual([]);
  });

  test("EMA(20) renders a server series, bar type switches, and a quick run shows results", async ({
    page,
  }) => {
    const errors = collectConsoleErrors(page);
    await page.goto(chartUrl);
    await waitForChart(page);

    const preview = page.waitForResponse(
      (response) => response.url().includes("/api/indicators/preview") && response.status() === 200,
    );
    await page.locator("#indicator-add").selectOption("ema");
    await page.getByRole("button", { name: "Add", exact: true }).click();
    await preview;
    await expect(page.getByText(/server preview: [\d,]+ values/)).toBeVisible();
    await expect(page.locator('[aria-label="Period"]')).toHaveValue("20");

    const fiveMinuteBars = page.waitForResponse(
      (response) => response.url().includes("bar_type=5m") && response.status() === 200,
    );
    await page.locator('[aria-label="Chart timeframe"]').selectOption("5m");
    await fiveMinuteBars;
    await expect(page.getByText("5 min bars")).toBeVisible();
    await waitForChart(page);

    await page.locator('[aria-label="Fast EMA period"]').fill("5");
    await expect(page.locator('[aria-label="Fast EMA period"]')).toHaveValue("5");

    await runBacktest(page);
    const drawer = page.getByLabel("Backtest results");
    await expect(drawer.getByText("Net P&L · historical simulation")).toBeVisible();
    await expect(drawer.locator('[aria-label^="Equity curve in USD"]')).toBeVisible();
    await expect(drawer.getByText("Drawdown (%)")).toBeVisible();
    await expect(drawer.locator('[data-testid="fallback-badge"]')).toHaveCount(0);

    await drawer.getByRole("tab", { name: /Trades/ }).click();
    await expect(drawer.getByText("completed trade(s)")).toBeVisible();
    const firstTrade = drawer.getByRole("button", { name: /^T-\d{4}/ }).first();
    await expect(firstTrade).toBeVisible();
    await firstTrade.click();
    await expect(page.getByLabel("Trade inspector")).toBeVisible();
    await expect(page.getByLabel("Trade inspector").getByText("Execution source")).toBeVisible();
    await expect(page.getByLabel("Trade inspector").getByText("Max loss")).toBeVisible();

    // URL state round-trip: instrument, bar type, indicator and run id.
    const url = new URL(page.url());
    expect(url.searchParams.get("instrument")).toBe(INSTRUMENT);
    expect(url.searchParams.get("bar")).toBe("5m");
    expect(url.searchParams.get("run")).toBeTruthy();
    expect(url.searchParams.get("ind")).toContain("ema");
    expect(url.searchParams.get("params")).toContain('"fast_period":5');

    await page.reload();
    await waitForChart(page);
    await expect(page.locator('[aria-label="Chart timeframe"]')).toHaveValue("5m");
    await expect(page.locator('[aria-label="Fast EMA period"]')).toHaveValue("5");
    await expect(page.getByText(/server preview: [\d,]+ values/)).toBeVisible();
    await expect(
      page.getByLabel("Backtest results").getByText("complete", { exact: true }),
    ).toBeVisible({ timeout: 60_000 });
    expect(errors).toEqual([]);
  });

  test("Book preflight reports coverage in 2026 and an honest fallback before it", async ({
    page,
  }) => {
    const errors = collectConsoleErrors(page);
    await page.goto(chartUrl);
    await waitForChart(page);

    const coveragePreflight = page.waitForResponse(
      (response) => response.url().includes("/api/execution/preflight") && response.status() === 200,
    );
    await page.locator('input[name="execution-profile"][value="book"]').check();
    await coveragePreflight;
    await expect(page.getByText(/requested Book · effective Book/)).toBeVisible();
    await expect(page.getByText(/Book coverage: 2026-09-06/)).toBeVisible();
    await expect(page.locator('[data-testid="fallback-badge"]')).toHaveCount(0);
    expect(page.url()).toContain("profile=book");

    const fallbackPreflight = page.waitForResponse(
      (response) => response.url().includes("/api/execution/preflight") && response.status() === 200,
    );
    await page.goto(
      workspaceUrl({
        instrument: PRE_2026_INSTRUMENT,
        from: "2025-01-18",
        to: "2025-01-18",
        bar: "1m",
        profile: "book",
      }),
    );
    await fallbackPreflight;
    const badge = page.locator('[data-testid="fallback-badge"]').first();
    await expect(badge).toBeVisible();
    await expect(badge).toContainText("Requested Book, effective Conservative");
    await expect(badge).toContainText("no book coverage");
    await expect(page.getByText("Book coverage: not available")).toBeVisible();
    await expect(page.getByText(/priced at Conservative/)).toBeVisible();
    expect(errors).toEqual([]);
  });
});

test.describe("secondary routes and holdout", () => {
  test.beforeEach(async ({ page }) => {
    await requireApp(page);
  });

  test("data, runs and presets pages render without console errors", async ({ page }) => {
    const errors = collectConsoleErrors(page);
    for (const [path, heading] of [
      ["/data", "Data explorer"],
      ["/runs", "Runs"],
      ["/presets", "Presets"],
    ] as const) {
      await page.goto(path);
      await expect(page.getByRole("heading", { name: heading, level: 1 })).toBeVisible();
    }
    // The history table must show a completed run's net P&L, not "not available".
    await page.goto("/runs");
    const completeRow = page.locator("tbody tr").filter({ hasText: "complete" }).first();
    await expect(completeRow).toBeVisible();
    await expect(completeRow).toContainText("$");
    expect(errors).toEqual([]);
  });

  test("an out-of-sample reveal is logged and a second reveal warns", async ({ page, request }) => {
    const config = {
      strategy: {
        instrument_id: INSTRUMENT,
        bar_type: "1m",
        builtin_id: "nautilus_ema_cross",
        builtin_params: { fast_period: 9, slow_period: 21 },
        direction: "long",
      },
      execution: { profile: "conservative" },
      risk: { sizing: { mode: "fixed_stake", value: 50 }, max_trades: 50 },
      evaluation: {
        date_range: { from: "2026-09-16", to: "2026-09-19" },
        split: { train_end: "2026-09-17", test_start: "2026-09-19", embargo_days: 1 },
      },
    };
    const created = await request.post("/api/backtests", { data: config });
    expect(created.status()).toBe(202);
    const { run_id: runId } = (await created.json()) as { run_id: string };

    let status = "queued";
    for (let attempt = 0; attempt < 60 && status !== "complete" && status !== "failed"; attempt += 1) {
      await page.waitForTimeout(1000);
      const detail = await request.get(`/api/backtests/${runId}`);
      status = ((await detail.json()) as { status: string }).status;
    }
    expect(status).toBe("complete");

    await page.goto(workspaceUrl({ run: runId, instrument: INSTRUMENT }));
    const drawer = page.getByLabel("Backtest results");
    await expect(drawer).toBeVisible();
    const panel = drawer.getByLabel("Holdout ledger");
    await expect(panel).toBeVisible();

    // The ledger is append-only across test runs, so accept an already-revealed
    // window too: the reveal action and the repeat warning are what matter.
    const firstReveal = panel.getByRole("button", { name: "Reveal out-of-sample" });
    if ((await firstReveal.count()) > 0) await firstReveal.click();
    await expect(panel.getByText(/revealed \d+×/)).toBeVisible();
    await expect(panel.getByText(/has already been revealed/).first()).toBeVisible();

    await panel.getByRole("button", { name: "Reveal again", exact: true }).click();
    await panel.getByRole("button", { name: /Reveal again — window is no longer clean/ }).click();
    await expect(panel.getByText(/already been revealed \d+ time\(s\)/)).toBeVisible();
  });
});

test.describe("presets", () => {
  test.beforeEach(async ({ page }) => {
    await requireApp(page);
  });

  test("opening a preset restores quick parameters, sizing and profile", async ({ page, request }) => {
    const name = `e2e preset ${Date.now()}`;
    const config = {
      strategy: {
        instrument_id: INSTRUMENT,
        bar_type: "5m",
        builtin_id: "nautilus_ema_cross",
        builtin_params: { fast_period: 8, slow_period: 21 },
        direction: "long",
        indicators: [],
      },
      execution: { profile: "stress", fee_bps: 3, fill_timing: "next_bar_open" },
      risk: { sizing: { mode: "fixed_stake", value: 75 }, max_trades: 25 },
      evaluation: { date_range: { from: "2026-09-18", to: "2026-09-19" } },
    };
    const created = await request.post("/api/presets", { data: { name, config } });
    expect(created.status()).toBe(201);

    await page.goto("/presets");
    const row = page.getByRole("listitem").filter({ hasText: name });
    await expect(row).toBeVisible();
    await row.getByRole("link", { name: "Open" }).click();

    const rail = page.getByLabel("Strategy rail");
    await expect(rail.getByRole("button", { name: "Advanced" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await expect(page.getByLabel("Strategy configuration JSON")).toHaveValue(/"fast_period":\s*8/);
    await expect(page.locator('[aria-label="Chart timeframe"]')).toHaveValue("5m");
    await expect(page.locator('input[name="execution-profile"][value="stress"]')).toBeChecked();
    await expect(page.getByLabel("Stake in USD")).toHaveValue("75");
    await expect(page.getByLabel("Maximum trades")).toHaveValue("25");
    await expect(page.getByLabel("Fee in basis points")).toHaveValue("3");
  });
});

test("two runs compare side by side with a normalised equity overlay", async ({ page, request }) => {
  const health = await page.request.get("/api/health").catch(() => null);
  test.skip(!health || !health.ok(), "HTF_E2E app is not running");

  const listed = await request.get("/api/backtests?limit=50");
  const body = (await listed.json()) as { items: Array<{ run_id: string; status: string }> };
  const complete = body.items.filter((run) => run.status === "complete").slice(0, 2);
  test.skip(complete.length < 2, "needs two completed runs in var/");

  const [first, second] = complete;
  await page.goto(
    workspaceUrl({
      instrument: INSTRUMENT,
      run: first.run_id,
      compare: `${first.run_id},${second.run_id}`,
    }),
  );
  const drawer = page.getByLabel("Backtest results");
  await expect(drawer).toBeVisible();
  await drawer.getByRole("tab", { name: "Compare" }).click();

  await expect(drawer.getByText(/Equity overlay — normalised to 100 at the common start/)).toBeVisible();
  await expect(drawer.getByRole("columnheader", { name: "Net P&L $" })).toBeVisible();
  const rows = drawer.locator("table tbody tr");
  await expect(rows).toHaveCount(2);
  for (const id of [first.run_id, second.run_id]) {
    await expect(drawer.locator("table tbody")).toContainText(id.slice(0, 16));
  }
});
