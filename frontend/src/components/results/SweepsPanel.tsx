import { useMemo, useState } from "react";
import { api } from "../../api/client";
import type { BuiltinStrategy, Experiment, Instrument } from "../../api/types";
import { useApiResource } from "../../hooks/useApi";
import { fmtInteger, fmtTimestamp } from "../../lib/format";
import { ErrorState, LoadingState, StatusPill } from "../ui";

/**
 * Bounded one-axis sweep. The parent experiment owns standard child runs; it
 * never auto-promotes a winner and never hides a failed variant.
 */
export function SweepsPanel() {
  const experiments = useApiResource((signal) => api.experiments(signal), []);
  const strategies = useApiResource((signal) => api.strategies(signal), []);
  const instruments = useApiResource((signal) => api.instruments({ limit: 500 }, signal), []);

  const [name, setName] = useState("EMA parameter sweep");
  const [builtinId, setBuiltinId] = useState("nautilus_ema_cross");
  const [instrumentId, setInstrumentId] = useState("");
  const [axisPath, setAxisPath] = useState<string>("fast_period");
  const [valuesText, setValuesText] = useState("8, 12, 16");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<unknown | null>(null);

  const builtins: BuiltinStrategy[] = strategies.data?.builtins ?? [];
  const builtin = builtins.find((item) => item.id === builtinId) ?? builtins[0] ?? null;
  const parameters = builtin?.parameters ?? [];
  const items: Instrument[] = instruments.data?.instruments ?? [];
  const chosenInstrument =
    items.find((item) => item.instrument_id === instrumentId) ??
    items.find((item) => (item.trade_count ?? 0) >= 100) ??
    items[0] ??
    null;
  const effectiveInstrument = instrumentId || chosenInstrument?.instrument_id || "";
  const selectedParam =
    parameters.find((parameter) => parameter.name === axisPath) ?? parameters[0] ?? null;
  const experimentList: Experiment[] = useMemo(
    () => experiments.data?.experiments ?? [],
    [experiments.data],
  );

  const values = valuesText
    .split(",")
    .map((value) => Number(value.trim()))
    .filter((value) => Number.isFinite(value));

  const canSubmit =
    !!builtin &&
    !!selectedParam &&
    !!effectiveInstrument &&
    !!from &&
    !!to &&
    values.length > 0 &&
    values.length <= 50 &&
    !submitting;

  const submit = async () => {
    if (!builtin || !selectedParam) return;
    setSubmitting(true);
    setError(null);
    try {
      const base = {
        strategy: {
          instrument_id: effectiveInstrument,
          bar_type: "1m",
          builtin_id: builtin.id,
          builtin_params:
            builtin.id === "nautilus_ema_cross"
              ? { fast_period: 12, slow_period: 26 }
              : { ...(builtin.defaults ?? {}) },
        },
        execution: { profile: "conservative" },
        risk: { sizing: { mode: "fixed_stake", value: 100, bankroll_usd: 1000 } },
        evaluation: { date_range: { from, to } },
      };
      await api.createExperiment({
        name: name.trim() || `${builtin.label} sweep`,
        base,
        axes: [{ path: `strategy.builtin_params.${selectedParam.name}`, values }],
      });
      experiments.reload();
    } catch (caught) {
      setError(caught);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section className="panel overflow-hidden" aria-label="Experiments and sweeps">
      <div className="panel-title">
        <span>Experiments</span>
        <button type="button" className="btn-ghost text-[10px]" onClick={experiments.reload}>
          Refresh
        </button>
      </div>
      <div className="grid gap-3 border-b border-line p-3 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="grid gap-2 sm:grid-cols-2">
          <label>
            <span className="label">Name</span>
            <input className="input mt-1" value={name} onChange={(event) => setName(event.target.value)} />
          </label>
          <label>
            <span className="label">Built-in</span>
            <select
              className="input mt-1"
              value={builtin?.id ?? ""}
              onChange={(event) => {
                setBuiltinId(event.target.value);
                const next = builtins.find((item) => item.id === event.target.value);
                setAxisPath(next?.parameters?.[0]?.name ?? "");
              }}
            >
              {builtins.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <label className="sm:col-span-2">
            <span className="label">Instrument</span>
            <select
              className="input mt-1"
              value={effectiveInstrument}
              onChange={(event) => setInstrumentId(event.target.value)}
              aria-label="Sweep instrument"
            >
              {items.slice(0, 200).map((item) => (
                <option key={item.instrument_id} value={item.instrument_id}>
                  {item.question ?? item.instrument_id} ({fmtInteger(item.trade_count)} fills)
                </option>
              ))}
            </select>
          </label>
          <label>
            <span className="label">Parameter axis</span>
            <select
              className="input mt-1"
              value={selectedParam?.name ?? ""}
              onChange={(event) => setAxisPath(event.target.value)}
              disabled={parameters.length === 0}
            >
              {parameters.map((parameter) => (
                <option key={parameter.name} value={parameter.name}>
                  {parameter.label ?? parameter.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span className="label">Values (comma separated, max 50)</span>
            <input
              className="input mt-1"
              value={valuesText}
              onChange={(event) => setValuesText(event.target.value)}
              aria-label="Sweep values"
            />
          </label>
          <label>
            <span className="label">From</span>
            <input type="date" className="input mt-1" value={from} onChange={(event) => setFrom(event.target.value)} />
          </label>
          <label>
            <span className="label">To</span>
            <input type="date" className="input mt-1" value={to} onChange={(event) => setTo(event.target.value)} />
          </label>
          <div className="sm:col-span-2 flex items-center gap-2">
            <button type="button" className="btn-primary" disabled={!canSubmit} onClick={() => void submit()}>
              {submitting ? "Creating…" : `Create sweep (${values.length} variants)`}
            </button>
            <span className="text-[10px] text-ink-faint">
              One parent, standard child runs, concurrency 1. Every variant is retained.
            </span>
          </div>
          {error ? <div className="sm:col-span-2"><ErrorState error={error} title="Sweep could not be created" /></div> : null}
        </div>

        <div className="max-h-[260px] overflow-auto rounded border border-line">
          {experiments.loading ? (
            <LoadingState rows={4} label="Loading experiments" />
          ) : experiments.error ? (
            <ErrorState error={experiments.error} title="Experiments unavailable" onRetry={experiments.reload} />
          ) : experimentList.length === 0 ? (
            <p className="p-3 text-[11px] text-ink-muted">No experiments yet.</p>
          ) : (
            <table className="w-full text-[11px]">
              <caption className="sr-only">Experiments</caption>
              <thead className="sticky top-0 bg-surface-raised">
                <tr className="border-b border-line">
                  <th className="table-head px-2 py-1 text-left">Name</th>
                  <th className="table-head px-2 py-1 text-left">Status</th>
                  <th className="table-head px-2 py-1 text-right">Done</th>
                  <th className="table-head px-2 py-1" />
                </tr>
              </thead>
              <tbody>
                {experimentList.map((experiment) => (
                  <tr key={experiment.id} className="border-b border-line/60">
                    <td className="px-2 py-1">
                      <span className="block text-[10px]" title={experiment.id}>
                        {experiment.id}
                      </span>
                      <span className="text-[9px] text-ink-faint">{fmtTimestamp(experiment.created_at)}</span>
                    </td>
                    <td className="px-2 py-1"><StatusPill status={experiment.status} /></td>
                    <td className="numeric px-2 py-1 text-right">
                      {fmtInteger(experiment.completed)} / {fmtInteger(experiment.total)}
                    </td>
                    <td className="px-2 py-1 text-right">
                      <button
                        type="button"
                        className="btn-ghost text-[10px]"
                        onClick={() => void api.cancelExperiment(experiment.id).then(() => experiments.reload())}
                      >
                        Cancel
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </section>
  );
}
