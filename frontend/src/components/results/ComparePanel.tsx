import { useEffect, useMemo, useState } from "react";
import { api } from "../../api/client";
import type { CompareResponse, RunDetail, RunSummary } from "../../api/types";
import { fallbackWarning, resolveExecution } from "../../lib/execution";
import {
  fmtBps,
  fmtInteger,
  fmtNumber,
  fmtPct,
  fmtUsd,
} from "../../lib/format";
import { CompareEquityChart } from "./EquityChart";
import { netMetric } from "./MetricsGrid";
import { EmptyState, ErrorState, FallbackBadge, LoadingState, ProfileBadge } from "../ui";

function normaliseCompare(raw: CompareResponse | RunSummary[] | null): RunDetail[] {
  if (!raw) return [];
  if (Array.isArray(raw)) return raw as RunDetail[];
  return (raw.runs ?? []) as RunDetail[];
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

  useEffect(() => {
    if (compareIds.length < 2) {
      setRuns([]);
      setError(null);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    api
      .compare(compareIds, controller.signal)
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
  }, [compareIds, nonce]);

  const add = (id: string) => {
    const trimmed = id.trim();
    if (!trimmed || compareIds.includes(trimmed) || compareIds.length >= 3) return;
    onChange([...compareIds, trimmed]);
    setInput("");
  };

  const chartRuns = useMemo(
    () =>
      runs.map((run) => ({
        label: run.label ?? run.run_id.slice(0, 12),
        equity: run.equity ?? null,
      })),
    [runs],
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
            disabled={compareIds.includes(currentRunId) || compareIds.length >= 3}
          >
            Add current run
          </button>
        ) : null}
        <button
          type="button"
          className="btn"
          onClick={() => setNonce((value) => value + 1)}
          disabled={compareIds.length < 2 || loading}
        >
          Refresh
        </button>
        {compareIds.length > 0 ? (
          <button type="button" className="btn-ghost" onClick={() => onChange([])}>
            Clear
          </button>
        ) : null}
      </div>

      {compareIds.length === 0 ? (
        <EmptyState
          title="No runs selected"
          message="Compare two or three runs. The overlay is normalised to 100 at the common start; the same strategy hash under different profiles isolates cost differences."
        />
      ) : (
        <ul className="flex flex-wrap gap-1.5">
          {compareIds.map((id) => (
            <li key={id}>
              <span className="chip">
                <span className="numeric text-[10px]" title={id}>
                  {id.length > 22 ? `${id.slice(0, 22)}…` : id}
                </span>
                <button
                  type="button"
                  className="btn-ghost px-1 text-[10px]"
                  onClick={() => onChange(compareIds.filter((item) => item !== id))}
                  aria-label={`Remove run ${id} from comparison`}
                >
                  ✕
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}

      {compareIds.length === 1 ? (
        <p className="text-[10px] text-ink-faint">Add at least one more run to compare.</p>
      ) : null}

      {loading ? <LoadingState label="Loading comparison" rows={2} /> : null}
      {error ? <ErrorState error={error} title="Comparison failed" onRetry={() => setNonce((v) => v + 1)} /> : null}

      {runs.length >= 2 ? (
        <div className="space-y-2">
          <CompareEquityChart runs={chartRuns} />
          <div className="overflow-auto">
            <table className="w-full border-collapse text-[11px]">
              <caption className="sr-only">Metric comparison across selected runs</caption>
              <thead className="sticky top-0 bg-surface-raised">
                <tr className="border-b border-line">
                  <th scope="col" className="table-head px-2 py-1">Run</th>
                  <th scope="col" className="table-head px-2 py-1">Profile</th>
                  <th scope="col" className="table-head px-2 py-1">Holdout</th>
                  <th scope="col" className="table-head px-2 py-1 text-right">Net P&amp;L $</th>
                  <th scope="col" className="table-head px-2 py-1 text-right">Net return</th>
                  <th scope="col" className="table-head px-2 py-1 text-right">Sharpe</th>
                  <th scope="col" className="table-head px-2 py-1 text-right">Max DD</th>
                  <th scope="col" className="table-head px-2 py-1 text-right">Trades</th>
                  <th scope="col" className="table-head px-2 py-1 text-right">Fees $</th>
                  <th scope="col" className="table-head px-2 py-1 text-right">Total cost</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => {
                  const execution = resolveExecution(run);
                  const netReturn =
                    netMetric(run.metrics, "net_return_pct") ?? netMetric(run.metrics, "return_pct");
                  return (
                    <tr key={run.run_id} className="border-b border-line/60">
                      <td className="px-2 py-1">
                        <span className="numeric block text-[10px] text-ink" title={run.run_id}>
                          {run.label ?? `${run.run_id.slice(0, 16)}…`}
                        </span>
                        <span className="block text-[9px] text-ink-faint">
                          data {run.data_version ? `${run.data_version.slice(0, 14)}…` : "not available"}
                        </span>
                      </td>
                      <td className="px-2 py-1">
                        <ProfileBadge profile={execution.effective} prefix={null} />
                        <FallbackBadge
                          requested={execution.requested}
                          effective={execution.effective}
                          reason={execution.reason}
                        />
                      </td>
                      <td className="px-2 py-1 text-[10px] text-ink-muted">
                        {run.holdout
                          ? run.holdout.revealed || run.holdout.reveal_count > 0
                            ? `revealed ${run.holdout.reveal_count}×`
                            : "hidden"
                          : "not available"}
                      </td>
                      <td className="numeric px-2 py-1 text-right">
                        {fmtUsd(netMetric(run.metrics, "net_pnl_usd"), { signed: true })}
                      </td>
                      <td className="numeric px-2 py-1 text-right">
                        {fmtPct(netReturn, 2, { signed: true })}
                      </td>
                      <td className="numeric px-2 py-1 text-right">
                        {fmtNumber(netMetric(run.metrics, "sharpe"), 2)}
                      </td>
                      <td className="numeric px-2 py-1 text-right">
                        {fmtPct(netMetric(run.metrics, "max_drawdown_pct"), 2)}
                      </td>
                      <td className="numeric px-2 py-1 text-right">
                        {fmtInteger(netMetric(run.metrics, "trade_count"))}
                      </td>
                      <td className="numeric px-2 py-1 text-right">
                        {fmtUsd(run.costs?.fees_usd ?? netMetric(run.metrics, "fees_usd"))}
                      </td>
                      <td className="numeric px-2 py-1 text-right">
                        {fmtBps(
                          run.costs?.total_cost_bps ??
                            netMetric(run.metrics, "cost_bps_of_notional"),
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {runs.map((run) => {
            const execution = resolveExecution(run);
            const warning = fallbackWarning(
              execution.requested,
              execution.effective,
              execution.reason,
            );
            return warning ? (
              <p key={`${run.run_id}:warning`} className="text-[10px] leading-relaxed text-warn">
                <span className="numeric">{run.run_id.slice(0, 18)}</span>: {warning}
              </p>
            ) : null;
          })}
          <p className="text-[10px] text-ink-faint">
            Effective profiles are shown per row. Net figures are simulation output and imply no
            profitability. Changing execution or sizing does not reset the holdout count.
          </p>
        </div>
      ) : null}
    </div>
  );
}
