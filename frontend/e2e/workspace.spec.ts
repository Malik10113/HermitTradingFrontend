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
    await expect(drawer.locator('[data-testid="fallback-badge"]')).toHaveCount(0);

    await drawer.getByRole("tab", { name: /Trades/ }).click();
    await expect(drawer.getByText("completed trade(s)")).toBeVisible();
    const firstTrade = drawer.getByRole("button", { name: /^T-\d{4}/ }).first();
    await expect(firstTrade).toBeVisible();
    await firstTrade.click();
    await expect(page.getByLabel("Trade inspector")).toBeVisible();
    await expect(page.getByLabel("Trade inspector").getByText("Execution source")).toBeVisible();

    // URL state round-trip: instrument, bar type, indicator and run id.
    const url = new URL(page.url());
    expect(url.searchParams.get("instrument")).toBe(INSTRUMENT);
    expect(url.searchParams.get("bar")).toBe("5m");
    expect(url.searchParams.get("run")).toBeTruthy();
    expect(url.searchParams.get("ind")).toContain("ema");

    await page.reload();
    await waitForChart(page);
    await expect(page.locator('[aria-label="Chart timeframe"]')).toHaveValue("5m");
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
    await expect(page.getByText(/Book coverage: .*→/)).toBeVisible();
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
