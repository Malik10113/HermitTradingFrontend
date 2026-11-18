import { useMemo, useState } from "react";
import type {
  IndicatorInstance,
  IndicatorParameter,
  IndicatorPreviewResponse,
  IndicatorSpec,
} from "../api/types";
import { defaultParams, makeInstanceId } from "../lib/config";
import { INDICATOR_PALETTE } from "../lib/palette";
import { ErrorState } from "./ui";

export const INDICATOR_COLORS = INDICATOR_PALETTE;

interface IndicatorChipsProps {
  specs: IndicatorSpec[];
  specsLoading: boolean;
  specsError: unknown | null;
  onReloadSpecs: () => void;
  instances: IndicatorInstance[];
  onChange: (instances: IndicatorInstance[]) => void;
  preview: IndicatorPreviewResponse | null;
  previewLoading: boolean;
  previewError: unknown | null;
  previewMismatch: string | null;
}

function parameterInput(
  parameter: IndicatorParameter,
  value: number | boolean | string | undefined,
  onChange: (value: number | boolean) => void,
) {
  const isBoolean = parameter.type === "bool" || parameter.type === "boolean";
  if (isBoolean) {
    return (
      <input
        type="checkbox"
        checked={value === true}
        onChange={(event) => onChange(event.target.checked)}
        aria-label={parameter.label ?? parameter.name}
      />
    );
  }
  return (
    <input
      className="input-numeric w-20 py-0.5"
      type="number"
      value={typeof value === "number" ? value : ""}
      min={parameter.min ?? undefined}
      max={parameter.max ?? undefined}
      step={parameter.step ?? undefined}
      onChange={(event) => {
        const parsed = Number(event.target.value);
        if (Number.isFinite(parsed)) onChange(parsed);
      }}
      aria-label={parameter.label ?? parameter.name}
    />
  );
}

export function IndicatorChips({
  specs,
  specsLoading,
  specsError,
  onReloadSpecs,
  instances,
  onChange,
  preview,
  previewLoading,
  previewError,
  previewMismatch,
}: IndicatorChipsProps) {
  const [addType, setAddType] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);

  const specsById = useMemo(
    () => new Map(specs.map((spec) => [spec.id, spec])),
    [specs],
  );

  const add = () => {
    const spec = specsById.get(addType);
    if (!spec) return;
    const instanceId = makeInstanceId(spec.id, instances);
    const instance: IndicatorInstance = {
      instance_id: instanceId,
      type: spec.id,
      params: defaultParams(spec),
      color: INDICATOR_COLORS[instances.length % INDICATOR_COLORS.length],
      visible: true,
    };
    onChange([...instances, instance]);
    setAddType("");
    setExpanded(instanceId);
  };

  const update = (instanceId: string, patch: Partial<IndicatorInstance>) => {
    onChange(
      instances.map((instance) =>
        instance.instance_id === instanceId ? { ...instance, ...patch } : instance,
      ),
    );
  };

  const setParam = (instanceId: string, name: string, value: number | boolean) => {
    const instance = instances.find((item) => item.instance_id === instanceId);
    if (!instance) return;
    update(instanceId, { params: { ...instance.params, [name]: value } });
  };

  const remove = (instanceId: string) => {
    onChange(instances.filter((instance) => instance.instance_id !== instanceId));
    if (expanded === instanceId) setExpanded(null);
  };

  const previewSummary = (() => {
    if (!preview) return null;
    if (previewMismatch) return `preview mismatch: ${previewMismatch}`;
    const keys = Object.keys(preview.series ?? {});
    const firstSeries = keys[0] ? Object.values(preview.series[keys[0]])[0] : undefined;
    const values = firstSeries?.length ?? 0;
    const warmup = preview.warmup ?? {};
    const warmups = Object.entries(warmup)
      .map(([id, bars]) => `${id}: ${bars == null ? "not available" : `${bars} bars`}`)
      .join(" · ");
    return `server preview: ${values.toLocaleString("en-GB")} values${warmups ? ` · ${warmups}` : ""}${
      preview.bars != null ? ` · ${preview.bars.toLocaleString("en-GB")} bars` : ""
    }`;
  })();

  return (
    <section className="panel" aria-label="Indicators">
      <div className="panel-title">
        <span>Indicators</span>
        <span className="flex items-center gap-1.5 normal-case tracking-normal">
          {previewLoading ? <span className="badge">preview…</span> : null}
          <span className="text-[10px] text-ink-faint">{instances.length} instance(s)</span>
        </span>
      </div>

      <div className="space-y-2 p-2">
        <div className="flex items-center gap-1.5">
          <label className="sr-only" htmlFor="indicator-add">
            Add indicator
          </label>
          <select
            id="indicator-add"
            className="input py-1"
            value={addType}
            onChange={(event) => setAddType(event.target.value)}
            disabled={specsLoading || specs.length === 0}
          >
            <option value="">
              {specsLoading ? "Loading registry…" : "Add indicator…"}
            </option>
            {specs.map((spec) => (
              <option key={spec.id} value={spec.id}>
                {spec.label} · {spec.pane}
              </option>
            ))}
          </select>
          <button type="button" className="btn" onClick={add} disabled={!addType}>
            Add
          </button>
        </div>

        {specsError ? (
          <ErrorState error={specsError} title="Indicator registry unavailable" onRetry={onReloadSpecs} />
        ) : null}

        {instances.length === 0 ? (
          <p className="px-1 py-1 text-[11px] leading-relaxed text-ink-muted">
            No indicators. Values are computed server-side and shared with the strategy rail —
            the chart never evaluates indicator math in JavaScript.
          </p>
        ) : (
          <ul className="space-y-1.5">
            {instances.map((instance) => {
              const spec = specsById.get(instance.type);
              const isOpen = expanded === instance.instance_id;
              const parameters = spec?.parameters ?? [];
              return (
                <li
                  key={instance.instance_id}
                  className="rounded border border-line bg-surface-sunken/60"
                >
                  <div className="flex flex-wrap items-center gap-1.5 px-1.5 py-1">
                    <input
                      type="color"
                      value={instance.color ?? INDICATOR_COLORS[0]}
                      onChange={(event) =>
                        update(instance.instance_id, { color: event.target.value })
                      }
                      aria-label={`Colour for ${instance.instance_id}`}
                      className="h-4 w-5 cursor-pointer rounded border border-line bg-transparent p-0"
                    />
                    <button
                      type="button"
                      className="text-left text-[12px] font-medium text-ink hover:underline"
                      onClick={() =>
                        setExpanded(isOpen ? null : instance.instance_id)
                      }
                      aria-expanded={isOpen}
                      aria-controls={`indicator-params-${instance.instance_id}`}
                    >
                      {spec?.label ?? instance.type}
                      <span className="numeric ml-1 text-[10px] text-ink-faint">
                        {instance.instance_id}
                      </span>
                    </button>
                    {spec?.warmup != null ? (
                      <span className="badge" title="Warm-up bars returned as nulls, never zero">
                        warm-up {spec.warmup}
                      </span>
                    ) : null}
                    <span className="ml-auto flex items-center gap-0.5">
                      <button
                        type="button"
                        className="btn-ghost"
                        onClick={() =>
                          update(instance.instance_id, {
                            visible: instance.visible === false,
                          })
                        }
                        aria-pressed={instance.visible !== false}
                        aria-label={`${instance.visible === false ? "Show" : "Hide"} ${
                          instance.instance_id
                        }`}
                        title={instance.visible === false ? "Show on chart" : "Hide from chart"}
                      >
                        {instance.visible === false ? "○" : "●"}
                      </button>
                      <button
                        type="button"
                        className="btn-ghost text-negative"
                        onClick={() => remove(instance.instance_id)}
                        aria-label={`Remove ${instance.instance_id}`}
                      >
                        ✕
                      </button>
                    </span>
                  </div>
                  {isOpen ? (
                    <div
                      id={`indicator-params-${instance.instance_id}`}
                      className="flex flex-wrap items-end gap-3 border-t border-line px-2 py-1.5"
                    >
                      {parameters.length === 0 ? (
                        <span className="text-[10px] text-ink-faint">
                          No parameters declared by the registry.
                        </span>
                      ) : (
                        parameters.map((parameter) => (
                          <label
                            key={parameter.name}
                            className="flex flex-col gap-0.5 text-[10px] text-ink-muted"
                          >
                            <span>
                              {parameter.label ?? parameter.name}
                              {parameter.min != null || parameter.max != null ? (
                                <span className="ml-1 text-ink-faint">
                                  [{parameter.min ?? "-∞"}, {parameter.max ?? "∞"}]
                                </span>
                              ) : null}
                            </span>
                            {parameterInput(
                              parameter,
                              instance.params[parameter.name],
                              (value) =>
                                setParam(instance.instance_id, parameter.name, value),
                            )}
                          </label>
                        ))
                      )}
                      <span className="ml-auto text-[10px] text-ink-faint">
                        {spec?.pane ?? "overlay"} pane · values in{" "}
                        {(spec?.outputs ?? ["value"])
                          .map((output) => (typeof output === "string" ? output : output.key))
                          .join(", ")}
                      </span>
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}

        {previewError ? (
          <p className="text-[10px] text-warn" role="status">
            Indicator preview unavailable. The chart will not invent values.
          </p>
        ) : previewSummary ? (
          <p
            className={`text-[10px] ${previewMismatch ? "text-warn" : "text-ink-faint"}`}
            role="status"
          >
            {previewSummary}
          </p>
        ) : null}
      </div>
    </section>
  );
}
