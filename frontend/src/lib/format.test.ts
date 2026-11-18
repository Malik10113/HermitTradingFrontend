import { describe, expect, it } from "vitest";
import {
  fmtBps,
  fmtContracts,
  fmtDate,
  fmtDuration,
  fmtGbp,
  fmtInteger,
  fmtNumber,
  fmtPct,
  fmtProbability,
  fmtProbabilityPoints,
  fmtRate,
  fmtTimestamp,
  fmtUsd,
  fmtVersion,
  NOT_AVAILABLE,
} from "../lib/format";

describe("formatting with units and missing values", () => {
  it("renders not available for every missing shape, never zero", () => {
    for (const value of [null, undefined, Number.NaN, Number.POSITIVE_INFINITY]) {
      expect(fmtUsd(value)).toBe(NOT_AVAILABLE);
      expect(fmtPct(value)).toBe(NOT_AVAILABLE);
      expect(fmtProbability(value)).toBe(NOT_AVAILABLE);
      expect(fmtDuration(value)).toBe(NOT_AVAILABLE);
    }
  });

  it("formats probabilities at the 0.001 venue tick", () => {
    expect(fmtProbability(0.4234)).toBe("0.423");
    expect(fmtProbability(1)).toBe("1.000");
    expect(fmtProbabilityPoints(0.024)).toBe("+0.024 pts");
  });

  it("formats USD with a currency unit and optional sign", () => {
    expect(fmtUsd(1234.5)).toBe("$1,234.50");
    expect(fmtUsd(-12.3)).toBe("-$12.30");
    expect(fmtUsd(12.3, { signed: true })).toBe("+$12.30");
    expect(fmtGbp(100)).toBe("$100.00");
  });

  it("distinguishes percent values from 0–1 ratios", () => {
    expect(fmtPct(12.34)).toBe("12.3%");
    expect(fmtPct(12.34, 2, { signed: true })).toBe("+12.34%");
    expect(fmtRate(0.625)).toBe("62.5%");
  });

  it("formats costs in basis points and probability points", () => {
    expect(fmtBps(10)).toBe("10.0 bps");
    expect(fmtBps(2.5, 2)).toBe("2.50 bps");
  });

  it("formats durations without inventing a nominal match length", () => {
    expect(fmtDuration(42)).toBe("42s");
    expect(fmtDuration(3720)).toBe("1h 2m");
    expect(fmtDuration(200000)).toBe("2d 7h");
  });

  it("renders UTC timestamps deterministically", () => {
    expect(fmtTimestamp("2024-10-19T10:00:00Z")).toBe("2024-10-19 10:00:00 UTC");
    expect(fmtTimestamp("2024-10-19T10:00:00Z", false)).toBe("2024-10-19 10:00 UTC");
    expect(fmtTimestamp("not a date")).toBe(NOT_AVAILABLE);
    expect(fmtDate("2024-10-19T23:59:59Z")).toBe("2024-10-19");
  });

  it("formats counts, contracts, versions and numbers", () => {
    expect(fmtInteger(19961)).toBe("19,961");
    expect(fmtContracts(12.345)).toBe("12.35 contracts");
    expect(fmtNumber(0.1234, 3)).toBe("0.123");
    expect(fmtVersion("sha256:abcdef0123456789")).toBe("sha256:abcde…");
    expect(fmtVersion(null)).toBe(NOT_AVAILABLE);
  });
});
