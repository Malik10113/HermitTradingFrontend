import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useSearchParams } from "react-router-dom";
import { api, indicatorList } from "../api/client";
import { instrumentLabel } from "../api/normalise";
import type {
  BacktestConfig,
  Bar,
  Instrument,
  IndicatorInstance,
  IndicatorSpec,
} from "../api/types";
import { useApiResource, useDebouncedValue } from "../hooks/useApi";
import { useRun } from "../hooks/useRun";
import {
  loadRememberedDefaults,
  parseWorkspaceSearch,
  rememberWorkspaceSettings,
  serializeWorkspaceState,
  type PhaseFilter,
  type WorkspaceState,
} from "../lib/urlState";
import { BAR_ALIASES, BAR_DURATION_MS, CHART_RANGES, barTypeLabel } from "../lib/barTypes";
import { buildChartIndicators } from "../lib/chartIndicators";
import { addDaysIso, isoDateOnly, maxIso } from "../lib/date";
import { fmtInteger, fmtTimestamp, fmtVersion } from "../lib/format";
import { useMeta } from "../state/meta";
import { CommandPalette } from "../components/CommandPalette";
import { IndicatorChips } from "../components/IndicatorChips";
import { MarketBar } from "../components/MarketBar";
import { PriceChart } from "../components/PriceChart";
import { ResultsDrawer } from "../components/results/ResultsDrawer";
import { StrategyRail } from "../components/StrategyRail";
import { EmptyState, ErrorState, LoadingState, SourceNote } from "../components/ui";

const RANGE_DAYS: Record<string, number> = {
  "1m": 31,
  "3m": 92,
  "1y": 366,
};

// Captured when the module loads, before any router or effect can rewrite the
// address bar. A shared run URL must survive the first paint.
const BOOT_SEARCH = typeof window !== "undefined" ? window.location.search : "";

const PHASE_OPTIONS: Array<{ id: PhaseFilter; label: string }> = [
  { id: "all", label: "All" },
  { id: "pre_match", label: "Pre-match" },
  { id: "in_play", label: "In-play" },
  { id: "post_match", label: "Post-match" },
];

export function WorkspacePage() {
  const location = useLocation();
  const [, setSearchParams] = useSearchParams();
  const { meta } = useMeta();

  const initial = useRef<{ state: WorkspaceState; issues: string[] } | null>(null);
  if (initial.current === null) {
    // The module-level boot search is the only value that cannot have been
    // rewritten by an effect from an earlier render.
    const initialSearch = BOOT_SEARCH;
    const hasQuery = initialSearch.length > 1;
    initial.current = hasQuery
      ? parseWorkspaceSearch(initialSearch)
      : { state: loadRememberedDefaults(), issues: [] };
  }
  const [state, setState] = useState<WorkspaceState>(initial.current.state);
  const [urlIssues, setUrlIssues] = useState<string[]>(initial.current.issues);
  const [hydrated, setHydrated] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [railOpen, setRailOpen] = useState(true);
  const [drawerOpen, setDrawerOpen] = useState(!!state.runId);
  const [focusTradeId, setFocusTradeId] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<unknown | null>(null);
  const [copied, setCopied] = useState(false);
  const appliedRunRange = useRef<string | null>(null);
  const skipPersistState = useRef<WorkspaceState | null>(initial.current.state);

  const patch = (next: Partial<WorkspaceState>) =>
    setState((current) => ({ ...current, ...next }));

  const selectInstrument = (instrumentId: string) => {
    setState((current) => ({
      ...current,
      instrument: instrumentId,
      runId: "",
      compare: [],
    }));
    setUrlIssues([]);
  };

  // Restore a run id from the browser URL even if the first paint raced the
  // router. This effect is declared before the URL-sync effect on purpose.
  useEffect(() => {
    const params = new URLSearchParams(
      location.search.length > 1 ? location.search : window.location.search,
    );
    const runId = params.get("run");
    const instrumentId = params.get("instrument");
    if (!runId && !instrumentId) return;
    setState((current) => ({
      ...current,
      runId: current.runId || runId || "",
      instrument: current.instrument || instrumentId || "",
    }));
    // Initial hydration only; later changes are user-driven.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The router can commit its search string after the first render. Hydrate
  // the full shareable state once it is available, before URL synchronisation.
  useEffect(() => {
    const search = location.search.length > 1 ? location.search : window.location.search;
    if (search.length > 1) {
      const parsed = parseWorkspaceSearch(search);
      skipPersistState.current = parsed.state;
      setState(parsed.state);
      setUrlIssues(parsed.issues);
    }
    setHydrated(true);
    // Mount only: later navigation is user-driven and already in state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Keep the URL shareable and restore remembered settings after a reset.
  const serialised = useMemo(() => serializeWorkspaceState(state).toString(), [state]);
  useEffect(() => {
    if (!hydrated) return;
    const current = location.search.startsWith("?") ? location.search.slice(1) : location.search;
    const desired = new URLSearchParams(serialised);
    const browser = new URLSearchParams(window.location.search);
    // Never drop a run/instrument that arrived in the URL before state caught
    // up; the run is the shareable object, not a display preference.
    for (const key of ["run", "instrument"] as const) {
      if (!desired.get(key) && browser.get(key)) desired.set(key, browser.get(key)!);
    }
    const next = desired.toString();
    if (current !== next) {
      setSearchParams(desired, { replace: true });
    }
  }, [serialised, location.search, setSearchParams, hydrated]);

  useEffect(() => {
    // Do not overwrite the remembered view with a shared URL on first paint;
    // only user-driven changes update the remembered defaults.
    if (state === skipPersistState.current) {
      skipPersistState.current = null;
      return;
    }
    rememberWorkspaceSettings(state);
  }, [state]);

  useEffect(() => {
    if (state.runId) setDrawerOpen(true);
  }, [state.runId]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen(true);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const instrumentsResource = useApiResource(
    (signal) => api.instruments({ limit: 2000 }, signal),
    [],
  );
  const instruments = instrumentsResource.data?.instruments ?? [];

  useEffect(() => {
    if (!state.runId || state.instrument) return;
    // A shared run URL carries the run, not necessarily the instrument. Use
    // the run's own instrument and preserve the run id.
    let cancelled = false;
    void api
      .run(state.runId)
      .then((run) => {
        const iid = run.config?.strategy?.instrument_id;
        if (!cancelled && iid) setState((current) => ({ ...current, instrument: iid }));
      })
      .catch(() => {
        /* the drawer reports the run error */
      });
    return () => {
      cancelled = true;
    };
  }, [state.runId, state.instrument]);

  useEffect(() => {
    if (state.runId || state.instrument || instruments.length === 0) return;
    const first =
      instruments.find((instrument) => (instrument.trade_count ?? 0) >= 100) ??
      instruments[0];
    if (first?.instrument_id) selectInstrument(first.instrument_id);
    // selectInstrument is intentionally not a dependency: it closes over the
    // setter and would re-run on every state change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [instruments, state.instrument]);

  const selectedFromList = useMemo(
    () => instruments.find((instrument) => instrument.instrument_id === state.instrument) ?? null,
    [instruments, state.instrument],
  );
  const instrumentDetail = useApiResource(
    (signal) => api.instrument(state.instrument, signal),
    [state.instrument],
    !!state.instrument,
  );
  const selected: Instrument | null =
    instrumentDetail.data ??
    selectedFromList ??
    (state.instrument ? ({ instrument_id: state.instrument } satisfies Instrument) : null);

  const qualityResource = useApiResource(
    (signal) =>
      api.quality(
        state.instrument,
        { from: state.from || undefined, to: state.to || undefined },
        signal,
      ),
    [state.instrument, state.from, state.to],
    !!state.instrument,
  );

  const barsResource = useApiResource(
    (signal) =>
      api.bars(
        {
          instrument_id: state.instrument,
          bar_type: state.barType,
          from: state.from || undefined,
          to: state.to || undefined,
          limit: 20000,
        },
        signal,
      ),
    [state.instrument, state.barType, state.from, state.to],
    !!state.instrument,
  );

  const specsResource = useApiResource(
    async (signal) => indicatorList(await api.indicators(signal)),
    [],
  );
  const specs: IndicatorSpec[] = specsResource.data ?? [];

  const indicatorSignature = useMemo(
    () =>
      JSON.stringify(
        state.indicators.map((indicator) => ({
          instance_id: indicator.instance_id,
          type: indicator.type,
          params: indicator.params,
        })),
      ),
    [state.indicators],
  );
  const debouncedSignature = useDebouncedValue(indicatorSignature, 280);
  const previewInstances = useMemo(() => {
    try {
      const parsed = JSON.parse(debouncedSignature) as Array<
        Pick<IndicatorInstance, "instance_id" | "type" | "params">
      >;
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }, [debouncedSignature]);
  const previewResource = useApiResource(
    (signal) =>
      api.previewIndicator(
        {
          instrument_id: state.instrument,
          bar_type: state.barType,
          from: state.from || undefined,
          to: state.to || undefined,
          indicators: previewInstances,
        },
        signal,
      ),
    [state.instrument, state.barType, state.from, state.to, debouncedSignature],
    !!state.instrument && previewInstances.length > 0,
  );

  const bars: Bar[] = barsResource.data?.bars ?? [];  const chartIndicators = useMemo(
    () =>
      buildChartIndicators({
        bars,
        instances: state.indicators,
        preview: previewResource.data,
        specs,
      }),
    [bars, state.indicators, previewResource.data, specs],
  );

  useEffect(() => {
    if (!state.instrument) return;
    if (state.from && state.to) return;
    const from = isoDateOnly(
      qualityResource.data?.available_from ??
        instrumentDetail.data?.coverage?.from ??
        selectedFromList?.coverage?.from ??
        null,
    );
    const to = isoDateOnly(
      qualityResource.data?.available_to ??
        instrumentDetail.data?.coverage?.to ??
        selectedFromList?.coverage?.to ??
        null,
    );
    if ((!state.from && from) || (!state.to && to)) {
      const nextFrom = state.from || from || "";
      let nextTo = state.to || to || "";
      // A run requires start < end. A one-day coverage window is widened to the
      // next UTC day; the range is a query boundary, not a displayed datum.
      if (nextFrom && nextTo && nextFrom >= nextTo) nextTo = addDaysIso(nextFrom, 1);
      setState((current) => ({
        ...current,
        from: current.from || nextFrom,
        to: current.to || nextTo,
      }));
    }
  }, [
    state.instrument,
    state.from,
    state.to,
    qualityResource.data,
    instrumentDetail.data,
    selectedFromList,
  ]);

  const runController = useRun(state.runId || null);
  const run = runController.run;

  useEffect(() => {
    if (!run || appliedRunRange.current === run.run_id) return;
    appliedRunRange.current = run.run_id;
    const range = run.config?.evaluation?.date_range as
      | { from?: string; to?: string; start?: string; end?: string }
      | undefined;
    const from = isoDateOnly(range?.from ?? range?.start);
    const to = isoDateOnly(range?.to ?? range?.end);
    if (from && to) {
      setState((current) => ({ ...current, from, to }));
    }
  }, [run]);
  const lifecycle = selected?.lifecycle ?? run?.lifecycle ?? null;
  const stale = bars.some((bar) => bar.is_stale === true);

  const applyPresetRanges = (rangeId: string) => {
    if (rangeId === "all") {
      const from = isoDateOnly(qualityResource.data?.available_from ?? selected?.coverage?.from);
      let to = isoDateOnly(qualityResource.data?.available_to ?? selected?.coverage?.to);
      if (from && to && from >= to) to = addDaysIso(from, 1);
      patch({ from, to });
      return;
    }
    const days = RANGE_DAYS[rangeId];
    if (!days) return;
    const anchor = isoDateOnly(
      maxIso(
        state.to || null,
        maxIso(
          qualityResource.data?.available_to ?? null,
          selected?.coverage?.to ?? bars[bars.length - 1]?.ts_event?.slice(0, 10) ?? null,
        ),
      ),
    );
    if (!anchor) return;
    patch({ from: addDaysIso(anchor, -days), to: anchor });
  };

  const reset = () => {
    const remembered = loadRememberedDefaults();
    setState({ ...remembered, runId: "", compare: [] });
    setUrlIssues([]);
  };

  const copyLink = () => {
    if (typeof navigator !== "undefined" && navigator.clipboard) {
      navigator.clipboard.writeText(window.location.href).then(
        () => {
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1600);
        },
        () => setCopied(false),
      );
    }
  };

  const submitRun = async (config: BacktestConfig) => {
    setSubmitting(true);
    setSubmitError(null);
    try {
      const response = await api.createBacktest(config);
      setState((current) => ({ ...current, runId: response.run_id }));
      setDrawerOpen(true);
      setFocusTradeId(null);
    } catch (caught) {
      setSubmitError(caught);
    } finally {
      setSubmitting(false);
    }
  };

  const savePreset = async (name: string, config: BacktestConfig) => {
    await api.createPreset({ name, config });
  };

  const dataVersion = barsResource.data?.data_version ?? meta?.data_version ?? null;
  const fillCount = bars.every((bar) => typeof bar.trade_count === "number")
    ? bars.reduce((sum, bar) => sum + (bar.trade_count as number), 0)
    : null;
  const fullBarType = barsResource.data?.bar_type ?? barTypeLabel(state.barType);
  const barDuration = BAR_DURATION_MS[state.barType];
  const phaseAvailable = !!(lifecycle?.actual_kickoff_utc && lifecycle?.actual_end_utc);

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      <MarketBar
        instruments={instruments}
        instrumentsLoading={instrumentsResource.loading}
        instrumentsError={instrumentsResource.error}
        onReloadInstruments={instrumentsResource.reload}
        selected={selected}
        selectedId={state.instrument}
        onSelect={selectInstrument}
        league={state.league}
        onLeague={(next) => patch({ league: next })}
        onOpenPalette={() => setPaletteOpen(true)}
        quality={qualityResource.data}
        qualityLoading={qualityResource.loading}
      />

      {urlIssues.length > 0 ? (
        <div className="flex flex-wrap items-center gap-2 border-b border-warn/40 bg-warn/5 px-3 py-1 text-[10px] text-warn" role="status">
          <span>URL ignored some values:</span>
          {urlIssues.map((issue) => (
            <span key={issue} className="badge-warn">
              {issue}
            </span>
          ))}
          <button type="button" className="btn-ghost ml-auto" onClick={() => setUrlIssues([])}>
            Dismiss
          </button>
        </div>
      ) : null}

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden xl:flex-row">
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="flex flex-wrap items-center gap-2 border-b border-line bg-surface px-3 py-1.5">
            <div
              className="flex items-center gap-0.5"
              role="group"
              aria-label="Chart range"
              onKeyDown={(event) => {
                // Arrow keys move focus between range presets; Enter/Space keep
                // the native button activation behaviour.
                if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
                const buttons = Array.from(
                  event.currentTarget.querySelectorAll<HTMLButtonElement>("button"),
                );
                const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
                if (index < 0) return;
                event.preventDefault();
                const next =
                  event.key === "ArrowRight"
                    ? Math.min(index + 1, buttons.length - 1)
                    : Math.max(index - 1, 0);
                buttons[next]?.focus();
              }}
            >
              {CHART_RANGES.map((range) => (
                <button
                  key={range.id}
                  type="button"
                  className="btn px-2 py-0.5 text-[11px] focus-visible:ring-2 focus-visible:ring-accent focus-visible:outline-none"
                  onClick={() => applyPresetRanges(range.id)}
                  aria-label={`Set chart range to ${range.label}`}
                >
                  {range.label}
                </button>
              ))}
            </div>
            <span className="text-ink-faint" aria-hidden="true">|</span>
            <label className="flex items-center gap-1">
              <span className="label">Timeframe</span>
              <select
                className="input w-auto py-0.5 text-[11px]"
                value={state.barType}
                onChange={(event) =>
                  patch({ barType: event.target.value as WorkspaceState["barType"] })
                }
                aria-label="Chart timeframe"
              >
                {BAR_ALIASES.map((item) => (
                  <option key={item.alias} value={item.alias}>
                    {item.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex items-center gap-1">
              <span className="label">From</span>
              <input
                type="date"
                className="input w-auto py-0.5 text-[11px]"
                value={state.from}
                onChange={(event) => patch({ from: event.target.value })}
                aria-label="From date (UTC)"
                max={state.to || undefined}
              />
            </label>
            <label className="flex items-center gap-1">
              <span className="label">To</span>
              <input
                type="date"
                className="input w-auto py-0.5 text-[11px]"
                value={state.to}
                onChange={(event) => patch({ to: event.target.value })}
                aria-label="To date (UTC)"
                min={state.from || undefined}
              />
            </label>
            <div className="flex items-center gap-0.5" role="group" aria-label="Lifecycle phase filter">
              {PHASE_OPTIONS.map((phase) => (
                <button
                  key={phase.id}
                  type="button"
                  className={`btn px-2 py-0.5 text-[11px] ${
                    state.phase === phase.id ? "border-accent text-accent" : ""
                  }`}
                  onClick={() => patch({ phase: phase.id })}
                  disabled={!phaseAvailable && phase.id !== "all"}
                  aria-pressed={state.phase === phase.id}
                  title={
                    phaseAvailable
                      ? `Shade ${phase.label} window`
                      : "Phase unknown: no verified lifecycle row"
                  }
                >
                  {phase.label}
                </button>
              ))}
            </div>

            <span className="ml-auto flex items-center gap-1.5">
              <button type="button" className="btn-ghost text-[11px]" onClick={copyLink}>
                {copied ? "Link copied" : "Copy link"}
              </button>
              <button type="button" className="btn-ghost text-[11px]" onClick={reset}>
                Reset view
              </button>
              <button
                type="button"
                className="btn"
                onClick={() => setRailOpen((value) => !value)}
                aria-expanded={railOpen}
                aria-controls="strategy-rail"
              >
                {railOpen ? "Hide strategy" : "Show strategy"}
              </button>
            </span>
          </div>

          <div className="space-y-2 p-2">
            <section className="panel" aria-label="Price chart">
              <div className="panel-title">
                <span className="flex flex-wrap items-center gap-2 normal-case tracking-normal">
                  <span className="font-mono text-[11px] text-ink">
                    {selected ? instrumentLabel(selected) : "No instrument"}
                  </span>
                  <span className="badge">{barTypeLabel(state.barType)}</span>
                  {barDuration ? (
                    <span className="badge" title="Time bars built from fills; empty intervals have no bar">
                      {Math.round(barDuration / 60000)} min bars
                    </span>
                  ) : null}
                  <span className="badge" title={fullBarType}>
                    {fullBarType.length > 40 ? `${fullBarType.slice(0, 40)}…` : fullBarType}
                  </span>
                  <span className="numeric text-[10px] text-ink-faint">
                    data {fmtVersion(dataVersion)}
                  </span>
                  {stale ? <span className="badge-warn">stale source flag</span> : null}
                  {!phaseAvailable ? (
                    <span className="badge-warn" title="No verified kickoff/end lifecycle row">
                      phase unknown
                    </span>
                  ) : null}
                </span>
                <span className="flex items-center gap-2 text-[10px] normal-case tracking-normal text-ink-faint">
                  {barsResource.loading ? <span className="badge">bars…</span> : null}
                  {previewResource.loading ? <span className="badge">indicators…</span> : null}
                  {run ? (
                    <span className="badge" title={run.run_id}>
                      run {run.status}
                      {run.effective_profile ? ` · ${run.effective_profile}` : ""}
                    </span>
                  ) : null}
                </span>
              </div>

              {!state.instrument ? (
                <EmptyState
                  title="Select an instrument"
                  message="Search the acquired Polymarket markets with Ctrl+K, then choose YES or NO. Each outcome token is its own instrument and its own book."
                  action={
                    <button type="button" className="btn-primary" onClick={() => setPaletteOpen(true)}>
                      Search instruments
                    </button>
                  }
                />
              ) : barsResource.loading ? (
                <LoadingState label="Loading bars" rows={6} />
              ) : barsResource.error ? (
                <ErrorState
                  error={barsResource.error}
                  title="Bars could not be loaded"
                  onRetry={barsResource.reload}
                />
              ) : bars.length === 0 ? (
                <EmptyState
                  title="No bars in this range"
                  message="Empty intervals produce no bar, and no candle is fabricated. Widen the range or choose another bar type."
                  action={
                    <button type="button" className="btn" onClick={() => applyPresetRanges("all")}>
                      Show all available data
                    </button>
                  }
                />
              ) : (
                <PriceChart
                  bars={bars}
                  indicators={chartIndicators.series}
                  lifecycle={lifecycle}
                  trades={run?.trades ?? []}
                  signals={run?.signals ?? []}
                  phaseFilter={state.phase}
                  stale={stale}
                  onSelectTrade={(tradeId) => {
                    setFocusTradeId(tradeId);
                    setDrawerOpen(true);
                  }}
                />
              )}

              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-line px-3 py-1">
                <SourceNote>
                  {bars.length > 0
                    ? `${fmtInteger(bars.length)} bars · ${fmtInteger(fillCount)} fills · first ${fmtTimestamp(
                        bars[0]?.ts_event,
                        false,
                      )}`
                    : "No bar artifact loaded."}
                </SourceNote>
                <SourceNote>
                  Bars are built from fills; OHLC is last trade and volume is USD notional. Empty
                  minutes are gaps, not zero-volume candles.
                </SourceNote>
                {chartIndicators.mismatch ? (
                  <span className="badge-warn" role="status">
                    indicator series mismatch: {chartIndicators.mismatch}
                  </span>
                ) : null}
                {(run?.trades?.length ?? 0) > 0 || (run?.signals?.length ?? 0) > 0 ? (
                  <span className="text-[9px] text-ink-faint" aria-label="Marker legend">
                    markers: ▲ buy · ▼ sell · ● settlement
                  </span>
                ) : null}
              </div>
            </section>

            <IndicatorChips
              specs={specs}
              specsLoading={specsResource.loading}
              specsError={specsResource.error}
              onReloadSpecs={specsResource.reload}
              instances={state.indicators}
              onChange={(indicators) => patch({ indicators })}
              preview={previewResource.data}
              previewLoading={previewResource.loading}
              previewError={previewResource.error}
              previewMismatch={chartIndicators.mismatch}
            />
          </div>
        </div>

        <div
          id="strategy-rail"
          className={`min-h-0 shrink-0 border-line bg-surface-raised xl:h-full xl:border-l ${
            railOpen ? "block border-t xl:border-t-0 xl:w-[360px]" : "hidden"
          }`}
        >
          {railOpen ? (
            <StrategyRail
              instrumentId={state.instrument}
              barType={state.barType}
              from={state.from}
              to={state.to}
              indicators={state.indicators}
              specs={specs}
              builtinId={state.builtin}
              onBuiltinId={(builtin) => patch({ builtin })}
              initialQuickParams={state.quickParams}
              initialQuickDirection={state.quickDirection}
              onQuickConfig={(quickParams, quickDirection) =>
                setState((current) =>
                  JSON.stringify(current.quickParams) === JSON.stringify(quickParams) &&
                  current.quickDirection === quickDirection
                    ? current
                    : { ...current, quickParams, quickDirection },
                )
              }
              strategyJson={state.strategyJson}
              onStrategyJson={(strategyJson) => patch({ strategyJson })}
              profile={state.profile}
              onProfile={(profile) => patch({ profile })}
              quality={qualityResource.data}
              qualityLoading={qualityResource.loading}
              onRun={submitRun}
              submitting={submitting}
              submitError={submitError}
              runId={state.runId || null}
              onSavePreset={savePreset}
            />
          ) : null}
        </div>
      </div>

      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        instruments={instruments}
        selectedId={state.instrument}
        onSelect={selectInstrument}
      />

      <ResultsDrawer
        runId={state.runId || null}
        run={run}
        loading={runController.loading}
        error={runController.error}
        refresh={runController.refresh}
        reveal={runController.reveal}
        revealing={runController.revealing}
        revealError={runController.revealError}
        open={drawerOpen}
        onOpen={() => setDrawerOpen(true)}
        onClose={() => setDrawerOpen(false)}
        compareIds={state.compare}
        onCompareChange={(compare) => patch({ compare })}
        focusTradeId={focusTradeId}
      />

      {state.instrument && !selectedFromList && !instrumentsResource.loading ? (
        <p className="sr-only">
          The selected instrument is not in the instrument inventory. Bars are requested directly.
        </p>
      ) : null}
    </div>
  );
}
