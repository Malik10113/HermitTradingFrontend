import { useEffect, useMemo, useState } from "react";
import { api } from "../../api/client";
import type { CompareResponse, RunDetail, RunSummary } from "../../api/types";
import { fallbackWarning, profileLabel, resolveExecution } from "../../lib/execution";
import {
  fmtBps,
  fmtInteger,
  fmtNumber,
  fmtPct,
  fmtRate,
  fmtUsd,
} from "../../lib/format";
import { CompareEquityChart } from "./EquityChart";
import { grossMetric, netMetric } from "./MetricsGrid";
import { EmptyState, ErrorState, FallbackBadge, LoadingState, ProfileBadge } from "../ui";

const MAX_COMPARE_RUNS = 3;

function normaliseCompare(raw: CompareResponse | RunSummary[] | null): RunDetail[] {
  if (!raw) return [];
  if (Array.isArray(raw)) return raw as RunDetail[];
  return (raw.runs ?? []) as RunDetail[];
}

export interface CompareRow {
  run: RunDetail;
  execution: ReturnType<typeof resolveExecution>;
  grossPnl: number | null;
  netPnl: number | null;
  grossReturn: number | null;
  netReturn: number | null;
  grossSharpe: number | null;
  netSharpe: number | null;
  maxDrawdown: number | null;
  trades: number | null;
  fees: number | null;
  slippage: number | null;
  totalCostBps: number | null;
  fallbackRate: number | null;
}

/**
 * Flatten gross and net metrics into one comparable row per run. Missing
 * values stay null so the table can say "not available" rather than zero.
 */
export function compareRows(runs: RunDetail[]): CompareRow[] {
  return runs.map((run) => {
    const execution = resolveExecution(run);
    return {
      run,
      execution,
      grossPnl: grossMetric(run.metrics, "gross_pnl_usd"),
      netPnl: netMetric(run.metrics, "net_pnl_usd"),
      grossReturn: grossMetric(run.metrics, "gross_return_pct"),
      netReturn:
        netMetric(run.metrics, "net_return_pct") ?? netMetric(run.metrics, "return_pct"),
      grossSharpe: grossMetric(run.metrics, "gross_sharpe"),
      netSharpe: netMetric(run.metrics, "sharpe"),
      maxDrawdown: netMetric(run.metrics, "max_drawdown_pct"),
      trades: netMetric(run.metrics, "trade_count"),
      fees: run.costs?.fees_usd ?? netMetric(run.metrics, "fees_usd"),
      slippage: run.costs?.slippage_usd ?? netMetric(run.metrics, "slippage_usd"),
      totalCostBps:
        run.costs?.total_cost_bps ?? netMetric(run.metrics, "cost_bps_of_notional"),
      fallbackRate: run.costs?.fallback_rate ?? run.fallback_rate ?? null,
    };
  });
}

function runLabel(run: RunDetail): string {
  return run.label ?? run.run_id.slice(0, 16);
}

function CostSummary({ rows }: { rows: CompareRow[] }) {
  const bpsValues = rows
    .map((row) => row.totalCostBps)
    .filter((value): value is number => typeof value === "number");
  const bpsRange =
    bpsValues.length === 0
      ? "not available"
      : bpsValues.length === 1
        ? fmtBps(bpsValues[0])
        : `${fmtBps(Math.min(...bpsValues))} → ${fmtBps(Math.max(...bpsValues))}`;
  return (
    <div className="rounded border border-line bg-surface-sunken/40 px-2 py-1.5 text-[10px] leading-relaxed text-ink-muted">
      <div className="flex flex-wrap gap-x-4 gap-y-1">
        <span>
          <span className="label mr-1">Total cost</span>
          <span className="numeric">{bpsRange}</span>
        </span>
        <span>
          <span className="label mr-1">Fees</span>
          <span className="numeric">
            {rows.map((row) => fmtUsd(row.fees)).join(" / ")}
          </span>
        </span>
        <span>
          <span className="label mr-1">Slippage</span>
          <span className="numeric">
            {rows.map((row) => fmtUsd(row.slippage)).join(" / ")}
          </span>
        </span>
      </div>
      <p className="mt-1">
        Gross is after spread and slippage embedded in the executed fill prices, before fees. Net
        is gross less the separate fee line; slippage is not subtracted twice. Comparing the same
        strategy hash across profiles isolates cost, not skill. Net figures are historical
        simulation output and imply no profitability.
      </p>
    </div>
  );
}

export function ComparePanel({
  compareIds,
  onChange,
  currentRunId,
}: {
  compareIds: string[];
  onChange: (ids: string[]) => void;
  currentRunId: string | null;
}) {
  const [runs, setRuns] = useState<RunDetail[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown | null>(null);
  const [input, setInput] = useState("");
  const [nonce, setNonce] = useState(0);

  const ids = useMemo(
    () => compareIds.slice(0, MAX_COMPARE_RUNS),
    [compareIds],
  );

  useEffect(() => {
    if (ids.length < 2) {
      setRuns([]);
      setError(null);
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    api
      .compare(ids, controller.signal)
      .then((raw) => {
        setRuns(normaliseCompare(raw));
        setError(null);
      })
      .catch((caught: unknown) => {
        if (controller.signal.aborted) return;
        setError(caught);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [ids, nonce]);

  const add = (id: string) => {
    const trimmed = id.trim();
    if (!trimmed || ids.includes(trimmed) || ids.length >= MAX_COMPARE_RUNS) return;
    onChange([...ids, trimmed]);
    setInput("");
  };

  const chartRuns = useMemo(
    () =>
      runs.map((run) => ({
        label: runLabel(run),
        equity: run.equity ?? null,
      })),
    [runs],
  );

  const rows = useMemo(() => compareRows(runs), [runs]);
  const requestedProfiles = useMemo(
    () => new Set(rows.map((row) => row.execution.requested ?? "none")),
    [rows],
  );
  const effectiveProfiles = useMemo(
    () => new Set(rows.map((row) => row.execution.effective ?? "none")),
    [rows],
  );

  return (
    <div className="space-y-2 p-2">
      <div className="flex flex-wrap items-end gap-2">
        <label className="min-w-[220px] flex-1">
          <span className="label">Add run id (2–3 runs)</span>
          <input
            className="input mt-1"
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder="run id"
            aria-label="Run id to compare"
            onKeyDown={(event) => {
              if (event.key === "Enter") add(input);
            }}
          />
        </label>
        <button type="button" className="btn" onClick={() => add(input)} disabled={!input.trim()}>
          Add
        </button>
        {currentRunId ? (
          <button
            type="button"
            className="btn"
            onClick={() => add(currentRunId)}
            disabled={ids.includes(currentRunId) || ids.length >= MAX_COMPARE_RUNS}
          >
            Add current run
          </button>
        ) : null}
        <button
          type="button"
          className="btn"
          onClick={() => setNonce((value) => value + 1)}
          disabled={ids.length < 2 || loading}
        >
          Refresh
        </button>
        {ids.length > 0 ? (
          <button type="button" className="btn-ghost" onClick={() => onChange([])}>
            Clear
          </button>
        ) : null}
      </div>

      {ids.length === 0 ? (
        <EmptyState
          title="No runs selected"
          message="Compare two or three runs. The overlay is normalised to 100 at the common start; the same strategy hash under different profiles isolates cost differences."
        />
      ) : (
        <ul className="flex flex-wrap gap-1.5" aria-label="Runs in this comparison">
          {ids.map((id) => (
            <li key={id}>
              <span className="chip">
                <span className="numeric text-[10px]" title={id}>
                  {id.length > 22 ? `${id.slice(0, 22)}…` : id}
                </span>
                <button
                  type="button"
                  className="btn-ghost px-1 text-[10px]"
                  onClick={() => onChange(ids.filter((item) => item !== id))}
                  aria-label={`Remove run ${id} from comparison`}
                >
                  ✕
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}

      {ids.length === 1 ? (
        <p className="text-[10px] text-ink-faint">Add at least one more run to compare.</p>
      ) : null}
      {compareIds.length > MAX_COMPARE_RUNS ? (
        <p className="text-[10px] text-warn" role="status">
          Only the first {MAX_COMPARE_RUNS} runs are compared.
        </p>
      ) : null}

      {loading ? <LoadingState label="Loading comparison" rows={2} /> : null}
      {error ? (
        <ErrorState
          error={error}
          title="Comparison failed"
          onRetry={() => setNonce((value) => value + 1)}
        />
      ) : null}

      {rows.length >= 2 ? (
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="label">Profiles</span>
            {rows.map((row) => (
              <span key={row.run.run_id} className="inline-flex items-center gap-1">
                <span className="numeric text-[9px] text-ink-faint">{runLabel(row.run)}</span>
                <ProfileBadge profile={row.execution.effective} prefix={null} />
                <FallbackBadge
                  requested={row.execution.requested}
                  effective={row.execution.effective}
                  reason={row.execution.reason}
                />
              </span>
            ))}
            {requestedProfiles.size > 1 || effectiveProfiles.size > 1 ? (
              <span className="text-[10px] text-ink-faint">
                Profiles differ; cost differences are not strategy differences.
              </span>
            ) : null}
          </div>

          <CompareEquityChart runs={chartRuns} />

          <div className="overflow-x-auto">
            <table className="w-full min-w-[980px] border-collapse text-[11px]">
              <caption className="sr-only">
                Gross and net metric comparison across selected runs, with the effective execution
                profile and cost context.
              </caption>
              <thead className="sticky top-0 bg-surface-raised">
                <tr className="border-b border-line">
                  <th scope="col" rowSpan={2} className="table-head px-2 py-1 text-left">Run</th>
                  <th scope="col" rowSpan={2} className="table-head px-2 py-1 text-left">Profile</th>
                  <th scope="col" rowSpan={2} className="table-head px-2 py-1 text-left">Holdout</th>
                  <th scope="colgroup" colSpan={2} className="table-head px-2 py-1 text-center">
                    P&amp;L (USD)
                  </th>
                  <th scope="colgroup" colSpan={2} className="table-head px-2 py-1 text-center">
                    Return
                  </th>
                  <th scope="colgroup" colSpan={2} className="table-head px-2 py-1 text-center">
                    Sharpe
                  </th>
                  <th scope="col" rowSpan={2} className="table-head px-2 py-1 text-right">Max DD</th>
                  <th scope="col" rowSpan={2} className="table-head px-2 py-1 text-right">Trades</th>
                  <th scope="col" rowSpan={2} className="table-head px-2 py-1 text-right">Fees</th>
                  <th scope="col" rowSpan={2} className="table-head px-2 py-1 text-right">
                    Total cost
                  </th>
                </tr>
                <tr className="border-b border-line">
                  <th
                    scope="col"
                    className="table-head px-2 py-1 text-right"
                    title="After spread and slippage embedded in fill prices, before fees"
                  >
                    Gross
                  </th>
                  <th
                    scope="col"
                    className="table-head px-2 py-1 text-right"
                    title="Gross less fees; slippage remains embedded in the fills"
                  >
                    Net
                  </th>
                  <th
                    scope="col"
                    className="table-head px-2 py-1 text-right"
                    title="After spread and slippage embedded in fill prices, before fees"
                  >
                    Gross
                  </th>
                  <th
                    scope="col"
                    className="table-head px-2 py-1 text-right"
                    title="Gross less fees; slippage remains embedded in the fills"
                  >
                    Net
                  </th>
                  <th
                    scope="col"
                    className="table-head px-2 py-1 text-right"
                    title="After spread and slippage embedded in fill prices, before fees"
                  >
                    Gross
                  </th>
                  <th
                    scope="col"
                    className="table-head px-2 py-1 text-right"
                    title="Gross less fees; slippage remains embedded in the fills"
                  >
                    Net
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.run.run_id} className="border-b border-line/60">
                    <td className="px-2 py-1">
                      <span className="numeric block text-[10px] text-ink" title={row.run.run_id}>
                        {runLabel(row.run)}
                      </span>
                      <span className="block text-[9px] text-ink-faint">
                        data {row.run.data_version ? `${row.run.data_version.slice(0, 14)}…` : "not available"}
                      </span>
                      <span className="block text-[9px] text-ink-faint">
                        strategy{" "}
                        {row.run.strategy_hash ? `${row.run.strategy_hash.slice(0, 12)}…` : "not available"}
                      </span>
                    </td>
                    <td className="px-2 py-1">
                      <ProfileBadge profile={row.execution.effective} prefix={null} />
                      <FallbackBadge
                        requested={row.execution.requested}
                        effective={row.execution.effective}
                        reason={row.execution.reason}
                      />
                      <span className="mt-0.5 block text-[9px] text-ink-faint">
                        requested {profileLabel(row.execution.requested)}
                      </span>
                    </td>
                    <td className="px-2 py-1 text-[10px] text-ink-muted">
                      {row.run.holdout
                        ? row.run.holdout.revealed || row.run.holdout.reveal_count > 0
                          ? `revealed ${fmtInteger(row.run.holdout.reveal_count)}×`
                          : "hidden"
                        : "not available"}
                    </td>
                    <td className="numeric px-2 py-1 text-right">
                      {fmtUsd(row.grossPnl, { signed: true })}
                    </td>
                    <td className="numeric px-2 py-1 text-right font-semibold">
                      {fmtUsd(row.netPnl, { signed: true })}
                    </td>
                    <td className="numeric px-2 py-1 text-right">
                      {fmtPct(row.grossReturn, 2, { signed: true })}
                    </td>
                    <td className="numeric px-2 py-1 text-right font-semibold">
                      {fmtPct(row.netReturn, 2, { signed: true })}
                    </td>
                    <td className="numeric px-2 py-1 text-right">{fmtNumber(row.grossSharpe, 2)}</td>
                    <td className="numeric px-2 py-1 text-right">{fmtNumber(row.netSharpe, 2)}</td>
                    <td className="numeric px-2 py-1 text-right">{fmtPct(row.maxDrawdown, 2)}</td>
                    <td className="numeric px-2 py-1 text-right">{fmtInteger(row.trades)}</td>
                    <td className="numeric px-2 py-1 text-right">{fmtUsd(row.fees)}</td>
                    <td className="numeric px-2 py-1 text-right">
                      {fmtBps(row.totalCostBps)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <CostSummary rows={rows} />

          {rows.map((row) => {
            const warning = fallbackWarning(
              row.execution.requested,
              row.execution.effective,
              row.execution.reason,
            );
            const fallbackRate =
              row.fallbackRate !== null ? `Fill fallback rate ${fmtRate(row.fallbackRate, 1)}.` : "";
            if (!warning && !fallbackRate) return null;
            return (
              <p key={`${row.run.run_id}:warning`} className="text-[10px] leading-relaxed text-warn" role="note">
                <span className="numeric">{runLabel(row.run)}</span>: {warning ?? ""} {fallbackRate}
              </p>
            );
          })}

          <p className="text-[10px] text-ink-faint">
            The equity overlay is indexed to 100 at the common start; index values are not returns
            and imply no profitability. Effective profiles stay attached to every net figure.
            Changing execution or sizing does not reset the holdout count.
          </p>
        </div>
      ) : null}
    </div>
  );
}
