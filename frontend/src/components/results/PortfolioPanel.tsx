import { useMemo, useState } from "react";
import { api } from "../../api/client";
import type {
  PortfolioMethod,
  PortfolioMemberResult,
  PortfolioOptimizeResponse,
  RunSummary,
} from "../../api/types";
import { NOT_AVAILABLE, fmtNumber, fmtPct, fmtRate, fmtUsd } from "../../lib/format";
import { ErrorState, LoadingState, ProfileBadge } from "../ui";

/** The API caps a portfolio at ten sleeves; RunsPage enforces the same cap. */
export const MAX_PORTFOLIO_RUNS = 10;

export interface PortfolioMethodOption {
  value: PortfolioMethod;
  label: string;
}

/**
 * One user-facing name per backend allocation method. The labels describe the
 * method, never a result: no option claims a better allocation.
 */
export const PORTFOLIO_METHODS: PortfolioMethodOption[] = [
  {
    value: "three_stage",
    label: "Three-stage (inv-vol + corr penalty + duplicate collapse)",
  },
  { value: "inverse_volatility", label: "Inverse volatility" },
  { value: "risk_parity", label: "Risk parity (equal risk contribution)" },
  { value: "shrunk_mean_variance", label: "Shrunk mean-variance" },
  { value: "exponential", label: "Exponentially decayed mean" },
  { value: "equal", label: "Equal weight" },
];

export function portfolioMethodLabel(
  method: PortfolioMethod | string | null | undefined,
): string {
  const match = PORTFOLIO_METHODS.find((option) => option.value === method);
  return match ? match.label : NOT_AVAILABLE;
}

/** Compact, copy-safe identifier for a table cell; the full id stays in `title`. */
function shortId(value: string, length = 12): string {
  return value.length > length ? `${value.slice(0, length)}…` : value;
}

/** Keep the numeric text as typed; parse and validate only on submit. */
function parseFraction(value: string): number {
  const trimmed = value.trim();
  if (!trimmed) return Number.NaN;
  return Number(trimmed);
}

function SummaryCard({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="rounded border border-line bg-surface-sunken/40 px-2 py-1">
      <span className="label block">{label}</span>
      <span className="numeric block text-[12px] font-semibold text-ink">{value}</span>
      {note ? <span className="block text-[9px] text-ink-faint">{note}</span> : null}
    </div>
  );
}

function MemberTable({ members }: { members: PortfolioMemberResult[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[840px] border-collapse text-[11px]">
        <caption className="sr-only">
          Sleeve allocations across the completed runs, with the effective execution profile,
          Kelly fraction, annualised volatility and net simulated P&amp;L per sleeve.
        </caption>
        <thead className="bg-surface-raised">
          <tr className="border-b border-line">
            <th scope="col" className="table-head px-2 py-1 text-left">Run</th>
            <th scope="col" className="table-head px-2 py-1 text-left">Instrument</th>
            <th scope="col" className="table-head px-2 py-1 text-left">Profile</th>
            <th scope="col" className="table-head px-2 py-1 text-right">Weight</th>
            <th scope="col" className="table-head px-2 py-1 text-right">Kelly</th>
            <th scope="col" className="table-head px-2 py-1 text-right">Vol (ann.)</th>
            <th scope="col" className="table-head px-2 py-1 text-right">Sharpe</th>
            <th scope="col" className="table-head px-2 py-1 text-right">Net P&amp;L</th>
          </tr>
        </thead>
        <tbody>
          {members.map((member) => (
            <tr key={member.run_id} className="border-b border-line/60">
              <td className="px-2 py-1">
                <span className="numeric block text-[10px] text-ink" title={member.run_id}>
                  {shortId(member.run_id)}
                </span>
                {member.dropped ? (
                  <span className="badge-warn mt-0.5 inline-block text-[9px]" title="Collapsed out of the allocation">
                    dropped
                  </span>
                ) : null}
                {member.dropped ? (
                  <span className="block text-[9px] text-ink-faint">
                    {member.drop_reason ?? "drop reason not available"}
                  </span>
                ) : null}
              </td>
              <td className="numeric px-2 py-1 text-[10px] text-ink-muted">
                {member.instrument_id ?? NOT_AVAILABLE}
              </td>
              <td className="px-2 py-1">
                <ProfileBadge profile={member.effective_profile} prefix={null} />
              </td>
              <td className="numeric px-2 py-1 text-right">{fmtRate(member.weight, 2)}</td>
              <td className="numeric px-2 py-1 text-right">
                {fmtRate(member.kelly_fraction, 2)}
              </td>
              <td className="numeric px-2 py-1 text-right">{fmtRate(member.annualised_vol, 2)}</td>
              <td className="numeric px-2 py-1 text-right">{fmtNumber(member.sharpe, 2)}</td>
              <td className="numeric px-2 py-1 text-right">
                {fmtUsd(member.net_pnl_usd, { signed: true })}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CorrelationMatrix({ matrix }: { matrix: Record<string, Record<string, number>> }) {
  const ids = Object.keys(matrix);
  if (ids.length === 0) {
    return (
      <p className="text-[10px] text-ink-faint">
        Pair correlation matrix: {NOT_AVAILABLE}. Fewer than two sleeves remained after
        allocation.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table
        className="border-collapse text-[11px]"
        aria-label="Pair correlation matrix"
      >
        <caption className="sr-only">
          Pairwise correlation of daily sleeve returns, computed by the optimiser.
        </caption>
        <thead>
          <tr>
            <th scope="col" className="table-head px-2 py-1 text-left">Run</th>
            {ids.map((id) => (
              <th key={id} scope="col" className="table-head px-2 py-1 text-right" title={id}>
                {shortId(id)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {ids.map((rowId) => (
            <tr key={rowId} className="border-t border-line/60">
              <th scope="row" className="table-head px-2 py-1 text-left" title={rowId}>
                {shortId(rowId)}
              </th>
              {ids.map((colId) => (
                <td key={colId} className="numeric px-2 py-1 text-right">
                  {fmtNumber(matrix[rowId]?.[colId], 2)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function PortfolioPanel({
  selectedRunIds,
  runs = [],
}: {
  selectedRunIds: string[];
  runs?: RunSummary[];
}) {
  const [method, setMethod] = useState<PortfolioMethod>("three_stage");
  const [maxWeightText, setMaxWeightText] = useState("0.35");
  const [kellyText, setKellyText] = useState("0.25");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown | null>(null);
  const [result, setResult] = useState<PortfolioOptimizeResponse | null>(null);
  const [submittedIds, setSubmittedIds] = useState<string[]>([]);

  const maxWeight = parseFraction(maxWeightText);
  const kellyFraction = parseFraction(kellyText);
  const boundsValid =
    Number.isFinite(maxWeight) &&
    maxWeight >= 0.05 &&
    maxWeight <= 1 &&
    Number.isFinite(kellyFraction) &&
    kellyFraction >= 0.05 &&
    kellyFraction <= 1;
  const ready = selectedRunIds.length >= 2;
  const statusById = useMemo(() => {
    const map = new Map<string, RunSummary>();
    for (const run of runs) map.set(run.run_id, run);
    return map;
  }, [runs]);

  // A selection may include a queued or failed run; the optimiser only reads
  // completed runs, so say so before it silently drops one.
  const notComplete = selectedRunIds.filter((id) => {
    const run = statusById.get(id);
    return run ? run.status !== "complete" : false;
  });

  const stale =
    result !== null &&
    (submittedIds.length !== selectedRunIds.length ||
      submittedIds.some((id, index) => id !== selectedRunIds[index]));

  const optimise = async () => {
    if (!ready || loading || !boundsValid) return;
    const ids = [...selectedRunIds];
    setLoading(true);
    setError(null);
    try {
      const response = await api.portfolioOptimize({
        run_ids: ids,
        method,
        max_weight: maxWeight,
        kelly_fraction: kellyFraction,
      });
      setResult(response);
      setSubmittedIds(ids);
    } catch (caught) {
      setError(caught);
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="panel overflow-hidden" aria-label="Portfolio optimisation">
      <div className="panel-title">
        <div>
          <h2 className="text-[12px] font-semibold text-ink">Portfolio optimisation</h2>
          <p className="text-[10px] text-ink-muted">
            Allocate across 2–{MAX_PORTFOLIO_RUNS} selected runs: sleeve weights, Kelly
            fractions and the pair correlation matrix come from the server; nothing is
            estimated here.
          </p>
        </div>
        <span className="badge" title="Runs selected in the table above">
          {selectedRunIds.length} / {MAX_PORTFOLIO_RUNS} runs
        </span>
      </div>

      <div className="space-y-2 p-2">
        <div className="flex flex-wrap items-end gap-2">
          <label className="min-w-[260px] flex-1">
            <span className="label">Method</span>
            <select
              className="input mt-1"
              value={method}
              onChange={(event) => setMethod(event.target.value as PortfolioMethod)}
              aria-label="Allocation method"
            >
              {PORTFOLIO_METHODS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <label className="w-[130px]">
            <span className="label">Max sleeve weight</span>
            <input
              className="input mt-1"
              type="number"
              min={0.05}
              max={1}
              step={0.05}
              value={maxWeightText}
              onChange={(event) => setMaxWeightText(event.target.value)}
              aria-label="Max sleeve weight"
            />
          </label>
          <label className="w-[130px]">
            <span className="label">Kelly fraction</span>
            <input
              className="input mt-1"
              type="number"
              min={0.05}
              max={1}
              step={0.05}
              value={kellyText}
              onChange={(event) => setKellyText(event.target.value)}
              aria-label="Kelly fraction"
            />
          </label>
          <button
            type="button"
            className="btn"
            onClick={() => void optimise()}
            disabled={!ready || loading || !boundsValid}
            title={
              !ready
                ? "Select at least two runs to optimise a portfolio"
                : !boundsValid
                  ? "Max sleeve weight and Kelly fraction must be between 0.05 and 1.0"
                  : "Allocate capital across the selected runs"
            }
          >
            Optimise portfolio
          </button>
        </div>

        {!ready ? (
          <p className="text-[10px] text-ink-faint">
            Select at least two completed runs in the table above.
          </p>
        ) : null}
        {!boundsValid ? (
          <p className="text-[10px] text-negative" role="alert">
            Max sleeve weight and Kelly fraction must each be between 0.05 and 1.0.
          </p>
        ) : null}
        {notComplete.length > 0 ? (
          <p className="text-[10px] text-warn" role="status">
            {notComplete.length} selected run(s) are not complete; the optimiser only reads
            completed runs.
          </p>
        ) : null}
        {selectedRunIds.length > MAX_PORTFOLIO_RUNS ? (
          <p className="text-[10px] text-warn" role="status">
            Only the first {MAX_PORTFOLIO_RUNS} selected runs are sent.
          </p>
        ) : null}

        {loading ? <LoadingState label="Optimising portfolio" rows={2} /> : null}
        {error ? (
          <ErrorState
            error={error}
            title="Portfolio optimisation failed"
            onRetry={() => void optimise()}
          />
        ) : null}

        {result ? (
          <div className="space-y-2">
            {stale ? (
              <p className="text-[10px] text-warn" role="status">
                The selection changed since this allocation was computed; optimise again to
                update it.
              </p>
            ) : null}

            <div className="flex flex-wrap items-center gap-2">
              <span className="label">Method</span>
              <span className="badge">{portfolioMethodLabel(result.method)}</span>
              <span className="text-[10px] text-ink-faint">
                {fmtNumber(result.reallocated_weight, 2)} of weight reallocated per sleeve cap
              </span>
            </div>

            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
              <SummaryCard
                label="Effective sleeves"
                value={fmtNumber(result.effective_n, 2)}
                note="1 / Σ w²"
              />
              <SummaryCard
                label="Max pair correlation"
                value={fmtNumber(result.max_pair_correlation, 2)}
              />
              <SummaryCard
                label="Portfolio LCB Kelly"
                value={fmtRate(result.portfolio_kelly_fraction, 1)}
                note={`${fmtUsd(result.recommended_daily_stake_usd)} daily stake`}
              />
              <SummaryCard
                label="Combined Net P&L"
                value={fmtUsd(result.metrics.net_pnl_usd, { signed: true })}
                note="Historical simulation"
              />
              <SummaryCard
                label="Combined return"
                value={fmtPct(result.metrics.return_pct, 2, { signed: true })}
              />
              <SummaryCard label="Combined Sharpe" value={fmtNumber(result.metrics.sharpe, 2)} />
              <SummaryCard
                label="Max drawdown"
                value={fmtPct(result.metrics.max_drawdown_pct, 2)}
              />
              <SummaryCard
                label="Window"
                value={`${fmtNumber(result.metrics.days, 0)} day(s)`}
                note={`${fmtUsd(result.metrics.starting_cash_usd)} start`}
              />
            </div>

            <MemberTable members={result.members} />

            <CorrelationMatrix matrix={result.correlation_matrix} />

            {result.warnings.length > 0 ? (
              <ul
                className="list-disc space-y-0.5 pl-4 text-[10px] text-warn"
                aria-label="Portfolio warnings"
              >
                {result.warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            ) : null}

            <p className="text-[10px] leading-relaxed text-ink-faint">
              Provenance: <span className="numeric">{result.provenance}</span>
            </p>
            <p className="text-[10px] leading-relaxed text-ink-faint">
              Historical simulation allocation across completed runs; not financial advice or a
              profitability claim.
            </p>
          </div>
        ) : null}
      </div>
    </section>
  );
}
