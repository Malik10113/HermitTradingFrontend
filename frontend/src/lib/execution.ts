import type {
  EffectiveExecution,
  ExecutionProfile,
  ExecutionSection,
  PreflightResponse,
  RunSummary,
} from "../api/types";

export interface ProfileMeta {
  id: ExecutionProfile;
  label: string;
  costSource: "none" | "fixed" | "dynamic";
  fee: string;
  slippage: string;
  minAbs: string;
  description: string;
  fallback: ExecutionProfile | null;
  slippageBps: number;
  minAbsSlippage: number;
}

export const PROFILE_META: Record<ExecutionProfile, ProfileMeta> = {
  frictionless: {
    id: "frictionless",
    label: "Frictionless",
    costSource: "none",
    fee: "0",
    slippage: "0",
    minAbs: "0",
    description: "Null baseline: no fees and no slippage. Never a default.",
    fallback: null,
    slippageBps: 0,
    minAbsSlippage: 0,
  },
  optimistic: {
    id: "optimistic",
    label: "Optimistic",
    costSource: "fixed",
    fee: "venue",
    slippage: "0",
    minAbs: "0",
    description: "Venue fee only; no spread or impact.",
    fallback: null,
    slippageBps: 0,
    minAbsSlippage: 0,
  },
  conservative: {
    id: "conservative",
    label: "Conservative",
    costSource: "fixed",
    fee: "venue",
    slippage: "10 bps",
    minAbs: "0.001",
    description: "Default: venue fee plus 10 bps, floored at 0.001 probability.",
    fallback: null,
    slippageBps: 10,
    minAbsSlippage: 0.001,
  },
  stress: {
    id: "stress",
    label: "Stress",
    costSource: "fixed",
    fee: "venue",
    slippage: "25 bps",
    minAbs: "0.002",
    description: "Robustness check: venue fee plus 25 bps, floored at 0.002.",
    fallback: null,
    slippageBps: 25,
    minAbsSlippage: 0.002,
  },
  book: {
    id: "book",
    label: "Book",
    costSource: "dynamic",
    fee: "venue",
    slippage: "book walk",
    minAbs: "—",
    description:
      "Walks L2 depth. Falls back to Conservative when coverage or a fresh snapshot is absent.",
    fallback: "conservative",
    slippageBps: 0,
    minAbsSlippage: 0,
  },
};

export function profileLabel(profile: string | null | undefined): string {
  if (!profile) return "not available";
  const meta = PROFILE_META[profile as ExecutionProfile];
  return meta ? meta.label : profile;
}

export function defaultExecution(profile: ExecutionProfile, feeBps = 0): ExecutionSection {
  const meta = PROFILE_META[profile];
  const execution: ExecutionSection = {
    profile,
    fill_timing: "next_bar_open",
    fee_bps: profile === "frictionless" ? 0 : feeBps,
    slippage_bps: meta.slippageBps,
    min_abs_slippage: meta.minAbsSlippage,
  };
  if (profile === "book") {
    execution.book_staleness_s = 60;
    execution.max_depth_levels = 10;
    execution.fallback = "conservative";
  }
  return execution;
}

export function isFallback(
  requested: string | null | undefined,
  effective: string | null | undefined,
): boolean {
  return !!requested && !!effective && requested !== effective;
}

export function fallbackWarning(
  requested: string | null | undefined,
  effective: string | null | undefined,
  reason: string | null | undefined,
): string | null {
  if (!isFallback(requested, effective)) return null;
  const reasonText = reason ? reason.replaceAll("_", " ") : "no reason recorded";
  return `Requested ${profileLabel(requested)}, effective ${profileLabel(
    effective,
  )} — ${reasonText}. Fills are priced with ${
    effective === "conservative" ? "Conservative" : profileLabel(effective)
  } costs.`;
}

export function resolveExecution(
  run: Pick<RunSummary, "requested_profile" | "effective_profile" | "fallback_reason" | "execution">,
): { requested: string | null; effective: string | null; reason: string | null } {
  return {
    requested: run.execution?.requested ?? run.requested_profile ?? null,
    effective: run.execution?.effective ?? run.effective_profile ?? null,
    reason: run.execution?.fallback_reason ?? run.fallback_reason ?? null,
  };
}

export function preflightBanner(preflight: PreflightResponse | null): string | null {
  if (!preflight) return null;
  return fallbackWarning(preflight.requested, preflight.effective, preflight.fallback_reason);
}

export function costEstimateText(execution: EffectiveExecution | PreflightResponse | null): string {
  if (!execution?.cost_estimate) return "not available";
  const estimate = execution.cost_estimate;
  const parts: string[] = [];
  if (typeof estimate.fee_bps === "number") parts.push(`fee ${estimate.fee_bps.toFixed(2)} bps`);
  if (typeof estimate.slippage_bps === "number") {
    parts.push(`slippage ${estimate.slippage_bps.toFixed(2)} bps`);
  }
  if (typeof estimate.per_side_bps === "number") {
    parts.push(`total ${estimate.per_side_bps.toFixed(2)} bps/side`);
  }
  if (typeof estimate.per_side_price_points === "number") {
    parts.push(`${estimate.per_side_price_points.toFixed(3)} pts/side`);
  }
  return parts.length > 0 ? parts.join(" · ") : "not available";
}

/** Book coverage can be a timestamp window or a months window from the index. */
export function bookCoverageText(
  coverage: EffectiveExecution["book_coverage"] | null | undefined,
): string {
  if (!coverage) return "not available";
  if (coverage.first_month && coverage.last_month) {
    return `${coverage.first_month} → ${coverage.last_month}`;
  }
  if (coverage.from && coverage.to) {
    return `${coverage.from.slice(0, 10)} → ${coverage.to.slice(0, 10)}`;
  }
  if (coverage.from) return coverage.from.slice(0, 10);
  if (coverage.to) return coverage.to.slice(0, 10);
  return coverage.has_coverage === false ? "no coverage" : "not available";
}
