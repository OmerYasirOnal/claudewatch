// Pure-logic tests for the formatter helpers on appRoot.
// app.js attaches the factory to globalThis so we don't have to refactor
// it into ES modules.
import { describe, it, expect, beforeEach } from "vitest";
import "../app.js";

function newRoot() {
  return globalThis.__claudewatchAppRoot();
}

describe("fmtNum", () => {
  let r;
  beforeEach(() => { r = newRoot(); });

  it("returns '—' for null / undefined", () => {
    expect(r.fmtNum(null)).toBe("—");
    expect(r.fmtNum(undefined)).toBe("—");
  });

  it("returns '—' for NaN / Infinity", () => {
    expect(r.fmtNum(NaN)).toBe("—");
    expect(r.fmtNum(Infinity)).toBe("—");
    expect(r.fmtNum(-Infinity)).toBe("—");
  });

  it("formats trillions with T", () => {
    expect(r.fmtNum(1.5e12)).toBe("1.50T");
  });

  it("formats billions with B", () => {
    expect(r.fmtNum(2.3e9)).toBe("2.30B");
  });

  it("formats millions with M", () => {
    expect(r.fmtNum(7.5e6)).toBe("7.50M");
  });

  it("formats thousands with K", () => {
    expect(r.fmtNum(12_345)).toBe("12.3K");
  });

  it("returns plain string under 1k", () => {
    expect(r.fmtNum(42)).toBe("42");
    expect(r.fmtNum(0)).toBe("0");
    expect(r.fmtNum(999)).toBe("999");
  });

  it("handles negatives as plain string", () => {
    // The current implementation returns the raw stringified value
    // for negatives; lock the behaviour in so a future change is intentional.
    expect(r.fmtNum(-5)).toBe("-5");
  });
});

describe("fmtMoney", () => {
  let r;
  beforeEach(() => { r = newRoot(); });

  it("returns '—' for null / undefined", () => {
    expect(r.fmtMoney(null)).toBe("—");
    expect(r.fmtMoney(undefined)).toBe("—");
  });

  it("returns '—' for non-finite numbers", () => {
    expect(r.fmtMoney(NaN)).toBe("—");
    expect(r.fmtMoney(Infinity)).toBe("—");
  });

  it("formats with two decimals and dollar prefix", () => {
    expect(r.fmtMoney(0)).toBe("$0.00");
    expect(r.fmtMoney(1.2345)).toBe("$1.23");
    expect(r.fmtMoney(12.5)).toBe("$12.50");
  });

  it("accepts numeric strings", () => {
    expect(r.fmtMoney("3.7")).toBe("$3.70");
  });
});

describe("fmtElapsed", () => {
  let r;
  beforeEach(() => { r = newRoot(); });

  it("returns Xs only when under a minute", () => {
    expect(r.fmtElapsed(0)).toBe("0s");
    expect(r.fmtElapsed(45)).toBe("45s");
  });

  it("returns minutes + seconds when under an hour", () => {
    expect(r.fmtElapsed(65)).toBe("1m 5s");
    expect(r.fmtElapsed(3599)).toBe("59m 59s");
  });

  it("returns hours + minutes + seconds when over an hour", () => {
    expect(r.fmtElapsed(3600)).toBe("1h 0m 0s");
    expect(r.fmtElapsed(3661)).toBe("1h 1m 1s");
    expect(r.fmtElapsed(86461)).toBe("24h 1m 1s");
  });

  it("clamps negative seconds to 0", () => {
    expect(r.fmtElapsed(-100)).toBe("0s");
  });

  it("accepts null / falsy as 0", () => {
    expect(r.fmtElapsed(null)).toBe("0s");
    expect(r.fmtElapsed(undefined)).toBe("0s");
  });
});

describe("fmtTime", () => {
  let r;
  beforeEach(() => { r = newRoot(); });

  it("returns '—' for falsy", () => {
    expect(r.fmtTime(null)).toBe("—");
    expect(r.fmtTime("")).toBe("—");
    expect(r.fmtTime(undefined)).toBe("—");
  });

  it("returns a non-empty locale string for a valid ISO", () => {
    const out = r.fmtTime("2026-05-17T10:00:00Z");
    expect(typeof out).toBe("string");
    expect(out.length).toBeGreaterThan(0);
    expect(out).not.toBe("—");
  });
});

describe("relTime", () => {
  let r;
  beforeEach(() => { r = newRoot(); });

  it("returns '—' for falsy", () => {
    expect(r.relTime(null)).toBe("—");
    expect(r.relTime("")).toBe("—");
  });

  it("returns 'just now' for very recent timestamps", () => {
    const iso = new Date(Date.now() - 1000).toISOString();
    expect(r.relTime(iso)).toBe("just now");
  });

  it("returns Ns ago for tens of seconds", () => {
    const iso = new Date(Date.now() - 30_000).toISOString();
    expect(r.relTime(iso)).toMatch(/^\d+s ago$/);
  });

  it("returns minutes+seconds when within an hour", () => {
    const iso = new Date(Date.now() - 5 * 60_000).toISOString();
    expect(r.relTime(iso)).toMatch(/^\d+m \d+s ago$/);
  });

  it("returns hours+minutes for older timestamps", () => {
    const iso = new Date(Date.now() - 3 * 3600_000).toISOString();
    expect(r.relTime(iso)).toMatch(/^\d+h \d+m ago$/);
  });
});

describe("fmtDuration", () => {
  let r;
  beforeEach(() => { r = newRoot(); });

  it("returns 0s for falsy", () => {
    expect(r.fmtDuration(0)).toBe("0s");
    expect(r.fmtDuration(null)).toBe("0s");
  });

  it("seconds only when < 1m", () => {
    expect(r.fmtDuration(42)).toBe("42s");
  });

  it("Xm when < 1h", () => {
    expect(r.fmtDuration(125)).toBe("2m");
  });

  it("Xh Ym when ≥ 1h", () => {
    expect(r.fmtDuration(3661)).toBe("1h 1m");
  });
});
