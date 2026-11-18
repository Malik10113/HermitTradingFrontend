import { useState } from "react";
import type { ReactNode } from "react";
import { errorMessage } from "../api/client";
import type { ExecutionProfile, RunStatus } from "../api/types";
import { fallbackWarning, profileLabel } from "../lib/execution";
import { fmtVersion } from "../lib/format";

export function Panel({
  title,
  actions,
  children,
  className = "",
  bodyClassName = "p-3",
  id,
}: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  id?: string;
}) {
  return (
    <section className={`panel ${className}`} id={id}>
      {title !== undefined && (
        <header className="panel-title">
          <span>{title}</span>
          {actions ? <div className="flex items-center gap-1.5">{actions}</div> : null}
        </header>
      )}
      <div className={bodyClassName}>{children}</div>
    </section>
  );
}

const STATUS_STYLES: Record<RunStatus, string> = {
  queued: "badge",
  running: "badge-accent",
  complete: "badge-positive",
  failed: "badge-danger",
  cancelled: "badge-warn",
  orphaned: "badge-warn",
};

export function StatusPill({ status }: { status: RunStatus | string | null | undefined }) {
  if (!status) return <span className="badge">unknown</span>;
  const style = STATUS_STYLES[status as RunStatus] ?? "badge";
  return (
    <span className={style} role="status">
      {status}
    </span>
  );
}

export function GradeBadge({ grade }: { grade: string | null | undefined }) {
  if (!grade) return <span className="badge">grade not available</span>;
  const normalised = grade.toUpperCase();
  const style =
    normalised.startsWith("A")
      ? "badge-positive"
      : normalised.startsWith("B")
        ? "badge-accent"
        : normalised.startsWith("C")
          ? "badge"
          : "badge-warn";
  return <span className={style}>grade {normalised}</span>;
}

export function ProfileBadge({
  profile,
  prefix = "Net",
}: {
  profile: ExecutionProfile | string | null | undefined;
  prefix?: string | null;
}) {
  return (
    <span className="badge-accent" title={`Effective execution profile: ${profileLabel(profile)}`}>
      {prefix ? `${prefix} · ` : ""}
      {profileLabel(profile)}
    </span>
  );
}

/**
 * Visible whenever a run or preflight will not use the requested profile.
 * Net results must never appear without this context.
 */
export function FallbackBadge({
  requested,
  effective,
  reason,
  full = false,
}: {
  requested: ExecutionProfile | string | null | undefined;
  effective: ExecutionProfile | string | null | undefined;
  reason?: string | null;
  full?: boolean;
}) {
  const warning = fallbackWarning(requested, effective, reason);
  if (!warning) return null;
  return (
    <span
      className="badge-warn normal-case tracking-normal"
      role="note"
      data-testid="fallback-badge"
      title={warning}
    >
      <span aria-hidden="true">⚠</span>
      {full
        ? warning
        : `Fallback: ${profileLabel(requested)} → ${profileLabel(effective)}${
            reason ? ` (${reason.replaceAll("_", " ")})` : ""
          }`}
    </span>
  );
}

export function EmptyState({
  title,
  message,
  action,
}: {
  title: string;
  message: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-1 px-4 py-8 text-center">
      <p className="text-[13px] font-semibold text-ink">{title}</p>
      <p className="max-w-md text-[12px] leading-relaxed text-ink-muted">{message}</p>
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}

export function ErrorState({
  error,
  onRetry,
  title = "Request failed",
}: {
  error: unknown;
  onRetry?: () => void;
  title?: string;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-1 px-4 py-6 text-center" role="alert">
      <p className="text-[13px] font-semibold text-negative">{title}</p>
      <p className="max-w-lg break-words text-[12px] text-ink-muted">{errorMessage(error)}</p>
      {onRetry ? (
        <button type="button" className="btn mt-2" onClick={onRetry}>
          Retry
        </button>
      ) : null}
    </div>
  );
}

export function LoadingState({ label = "Loading…", rows = 3 }: { label?: string; rows?: number }) {
  return (
    <div className="space-y-2 p-3" aria-busy="true" aria-live="polite">
      <span className="sr-only">{label}</span>
      {Array.from({ length: rows }).map((_, index) => (
        <div key={index} className="skeleton h-4" style={{ width: `${95 - index * 12}%` }} />
      ))}
    </div>
  );
}

export function KeyValue({
  items,
  columns = 2,
}: {
  items: Array<{ label: string; value: ReactNode; title?: string }>;
  columns?: 1 | 2 | 3;
}) {
  const grid = columns === 1 ? "grid-cols-1" : columns === 3 ? "grid-cols-3" : "grid-cols-2";
  return (
    <dl className={`grid ${grid} gap-x-4 gap-y-2`}>
      {items.map((item) => (
        <div key={item.label} className="min-w-0">
          <dt className="label">{item.label}</dt>
          <dd className="numeric mt-0.5 break-words text-[12px] text-ink" title={item.title}>
            {item.value ?? "not available"}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function CopyButton({ value, label = "Copy" }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className="btn-ghost text-[10px]"
      onClick={() => {
        if (typeof navigator !== "undefined" && navigator.clipboard) {
          navigator.clipboard.writeText(value).then(
            () => {
              setCopied(true);
              window.setTimeout(() => setCopied(false), 1500);
            },
            () => setCopied(false),
          );
        }
      }}
      title={value}
      aria-label={`${label} ${value}`}
    >
      {copied ? "Copied" : label}
    </button>
  );
}

export function VersionChip({
  label,
  value,
  title,
}: {
  label: string;
  value: string | null | undefined;
  title?: string;
}) {
  return (
    <span className="inline-flex items-center gap-1" title={title ?? value ?? undefined}>
      <span className="label">{label}</span>
      <span className="numeric text-[10px] text-ink-muted">{fmtVersion(value)}</span>
    </span>
  );
}

export function SourceNote({ children }: { children: ReactNode }) {
  return <p className="text-[10px] leading-relaxed text-ink-faint">{children}</p>;
}
