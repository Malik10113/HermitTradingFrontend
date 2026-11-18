import type { ReactNode } from "react";
import type { MetricSet, Metrics } from "../../api/types";
import {
  fmtDuration,
  fmtInteger,
  fmtNumber,
  fmtPct,
  fmtProbability,
  fmtProbabilityPoints,
  fmtRate,
  fmtUsd,
  isNumber,
  metricTone,
} from "../../lib/format";
import { ProfileBadge, SourceNote } from "../ui";

function layer(metrics: Metrics | null | undefined, key: "net" | "gross"): MetricSet {
  if (!metrics) return {};
  return (metrics[key] ?? {}) as MetricSet;
}

export function netMetric(metrics: Metrics | null | undefined, key: keyof MetricSet): number | null {
  if (!metrics) return null;
  const nested = layer(metrics, "net")[key];
  if (isNumber(nested)) return nested;
  const flat = metrics[key];
  return isNumber(flat) ? flat : null;
}

export function grossMetric(metrics: Metrics | null | undefined, key: keyof MetricSet): number | null {
  if (!metrics) return null;
  const nested = layer(metrics, "gross")[key];
  if (isNumber(nested)) return nested;
  if (key === "gross_pnl_usd") return isNumber(metrics.gross_pnl_usd) ? metrics.gross_pnl_usd : null;
  if (key === "gross_return_pct") return isNumber(metrics.gross_return_pct) ? metrics.gross_return_pct : null;
  if (key === "gross_sharpe") return isNumber(metrics.gross_sharpe) ? metrics.gross_sharpe : null;
  return null;
}

function winRate(value: unknown): string {
  if (!isNumber(value)) return "not available";
  return value <= 1 ? fmtRate(value, 1) : fmtPct(value, 1);
}

function exposure(value: unknown): string {
  return fmtPct(value, 1);
}

function MetricCard({
  label,
  value,
  sub,
  title,
  tone = "neutral",
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  title?: string;
  tone?: "positive" | "negative" | "neutral";
}) {
  const toneClass =
    tone === "positive" ? "text-positive" : tone === "negative" ? "text-negative" : "text-ink";
  return (
    <div className="rounded border border-line bg-surface-sunken/50 px-2 py-1.5" title={title}>
      <dt className="label">{label}</dt>
      <dd className={`numeric mt-0.5 text-[14px] leading-tight ${toneClass}`}>{value}</dd>
      {sub ? <div className="numeric mt-0.5 text-[10px] text-ink-faint">{sub}</div> : null}
    </div>
  );
}

export function MetricsGrid({
  metrics,
  profile,
}: {
  metrics: Metrics | null | undefined;
  profile: string | null | undefined;
}) {
  const netPnl = netMetric(metrics, "net_pnl_usd");
  const grossPnl = grossMetric(metrics, "gross_pnl_usd");
  const netReturn = netMetric(metrics, "net_return_pct") ?? netMetric(metrics, "return_pct");
  const grossReturn = grossMetric(metrics, "gross_return_pct");
  const sharpe = netMetric(metrics, "sharpe");
  const maxDrawdown = netMetric(metrics, "max_drawdown_pct");
  const tradeCount = netMetric(metrics, "trade_count");
  const win = netMetric(metrics, "win_rate");
  const profitFactor = netMetric(metrics, "profit_factor");
  const expectancy = netMetric(metrics, "expectancy_usd");
  const avgWin = netMetric(metrics, "avg_win_usd");
  const avgLoss = netMetric(metrics, "avg_loss_usd");
  const holding = netMetric(metrics, "avg_holding_period_s");
  const exposurePct = netMetric(metrics, "exposure_pct");
  const fees = netMetric(metrics, "fees_usd");
  const slippage = netMetric(metrics, "slippage_usd");
  const entryProbability = netMetric(metrics, "avg_entry_probability");
  const exitProbability = netMetric(metrics, "avg_exit_probability");
  const probabilityMove = netMetric(metrics, "avg_probability_move_pts");
  const contracts = netMetric(metrics, "avg_contracts");
  const settlementPnl = netMetric(metrics, "settlement_pnl_usd");
  const tradingPnl = netMetric(metrics, "trading_pnl_usd");
  const heldCount = netMetric(metrics, "held_to_settlement_count");
  const earlyExits = netMetric(metrics, "early_exit_count");
  const returnCapital = netMetric(metrics, "return_per_capital_deployed_pct");
  const returnNotional = netMetric(metrics, "return_per_notional_pct");

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-4 rounded border border-line bg-surface-raised px-3 py-2">
        <div>
          <div className="label">Net P&L · historical simulation</div>
          <div
            className="numeric text-[22px] font-semibold leading-tight"
            style={{
              color:
                netPnl == null
                  ? "var(--ink-faint)"
                  : netPnl > 0
                    ? "var(--positive)"
                    : netPnl < 0
                      ? "var(--negative)"
                      : "var(--ink)",
            }}
          >
            {fmtUsd(netPnl, { signed: true })}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            <ProfileBadge profile={profile} prefix="Net" />
            <span className="numeric text-[10px] text-ink-faint">
              gross {fmtUsd(grossPnl, { signed: true })}
            </span>
          </div>
        </div>
        <div className="grid flex-1 grid-cols-2 gap-2 sm:grid-cols-4">
          <MetricCard
            label="Net return"
            value={fmtPct(netReturn, 2, { signed: true })}
            sub={`gross ${fmtPct(grossReturn, 2, { signed: true })}`}
            tone={metricTone(netReturn)}
          />
          <MetricCard label="Sharpe (daily, 365)" value={fmtNumber(sharpe, 2)} />
          <MetricCard
            label="Max drawdown"
            value={fmtPct(maxDrawdown, 2)}
            tone={maxDrawdown != null && maxDrawdown > 0 ? "negative" : "neutral"}
          />
          <MetricCard
            label="Trades"
            value={fmtInteger(tradeCount)}
            sub={metrics ? "one position per instrument" : undefined}
          />
        </div>
      </div>

      <div>
        <h4 className="mb-1 text-[10px] font-semibold uppercase tracking-[0.08em] text-ink-faint">
          Distribution
        </h4>
        <dl className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-5">
          <MetricCard label="Win rate" value={winRate(win)} />
          <MetricCard label="Profit factor" value={fmtNumber(profitFactor, 2)} />
          <MetricCard label="Expectancy" value={fmtUsd(expectancy, { signed: true })} />
          <MetricCard label="Average win" value={fmtUsd(avgWin, { signed: true })} />
          <MetricCard label="Average loss" value={fmtUsd(avgLoss, { signed: true })} />
          <MetricCard label="Average holding" value={fmtDuration(holding)} />
          <MetricCard label="Time in market" value={exposure(exposurePct)} />
          <MetricCard label="Fees" value={fmtUsd(fees)} title="Charged separately from slippage" />
          <MetricCard
            label="Slippage (embedded)"
            value={fmtUsd(slippage)}
            title="Embedded in fill prices, not a separate cash line"
          />
          <MetricCard
            label="Settlement vs trading"
            value={`${fmtUsd(settlementPnl, { signed: true })} / ${fmtUsd(tradingPnl, {
              signed: true,
            })}`}
          />
        </dl>
      </div>

      <div>
        <h4 className="mb-1 text-[10px] font-semibold uppercase tracking-[0.08em] text-ink-faint">
          Prediction-market measures
        </h4>
        <dl className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-5">
          <MetricCard label="Average entry probability" value={fmtProbability(entryProbability)} />
          <MetricCard label="Average exit probability" value={fmtProbability(exitProbability)} />
          <MetricCard
            label="Probability captured"
            value={fmtProbabilityPoints(probabilityMove)}
            title="YES-equivalent probability points; not a return"
          />
          <MetricCard label="Average contracts" value={fmtNumber(contracts, 1)} />
          <MetricCard label="Held to settlement" value={fmtInteger(heldCount)} />
          <MetricCard label="Early exits" value={fmtInteger(earlyExits)} />
          <MetricCard
            label="Return per $ capital"
            value={fmtPct(returnCapital, 2, { signed: true })}
          />
          <MetricCard
            label="Return per $ notional"
            value={fmtPct(returnNotional, 2, { signed: true })}
          />
        </dl>
      </div>

      <SourceNote>
        Gross excludes fees and slippage; net includes both and is always shown with its effective
        execution profile. These are historical simulation outputs from stored data, not a
        profitability claim or trading advice.
      </SourceNote>
    </div>
  );
}
