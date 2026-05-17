// Tests for the cost-forecast loader + card-visibility gate.
// Mirrors the style of tests/formatters.test.js — app.js attaches the
// factory to globalThis so we don't have to refactor it into ES modules.
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import "../app.js";

function newRoot() {
  return globalThis.__claudewatchAppRoot();
}

describe("loadForecast", () => {
  let r;
  beforeEach(() => {
    r = newRoot();
    r.forecastData = null;
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("parses the /api/forecast response into forecastData", async () => {
    const payload = {
      window_hours: 24,
      observed_cost_usd: 1.23,
      observed_session_count: 17,
      hourly_rate_usd: 0.0512,
      projection_24h_usd: 1.23,
      projection_7d_usd: 8.61,
      projection_30d_usd: 36.9,
      as_of: "2026-05-17T10:00:00+00:00",
    };
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => payload,
    });
    globalThis.fetch = fetchMock;

    await r.loadForecast();

    expect(fetchMock).toHaveBeenCalledWith("/api/forecast?window_hours=24");
    expect(r.forecastData).toEqual(payload);
  });

  it("leaves forecastData unchanged when fetch returns !ok", async () => {
    r.forecastData = { observed_cost_usd: 5 };
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 });
    await r.loadForecast();
    expect(r.forecastData).toEqual({ observed_cost_usd: 5 });
  });

  it("swallows network errors and leaves forecastData untouched", async () => {
    r.forecastData = null;
    globalThis.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    await r.loadForecast();
    expect(r.forecastData).toBeNull();
  });
});

describe("showForecastCard plan + cardVisibility gating", () => {
  let r;
  beforeEach(() => {
    r = newRoot();
  });

  it("returns true when plan==='api' and cardVisibility.cost is true", () => {
    r.config = { plan: "api" };
    r.cardVisibility = { ...r.cardVisibility, cost: true };
    expect(r.showForecastCard()).toBe(true);
  });

  it("returns false when plan is not 'api' (Max/Pro users)", () => {
    r.config = { plan: "max" };
    r.cardVisibility = { ...r.cardVisibility, cost: true };
    expect(r.showForecastCard()).toBe(false);
  });

  it("returns false when cardVisibility.cost is explicitly false", () => {
    r.config = { plan: "api" };
    r.cardVisibility = { ...r.cardVisibility, cost: false };
    expect(r.showForecastCard()).toBe(false);
  });

  it("treats undefined plan as 'api' (matches showCost default)", () => {
    r.config = {};
    r.cardVisibility = { ...r.cardVisibility, cost: true };
    expect(r.showForecastCard()).toBe(true);
  });
});

describe("forecast formatting via fmtMoney", () => {
  let r;
  beforeEach(() => { r = newRoot(); });

  it("formats observed cost as dollars with two decimals", () => {
    expect(r.fmtMoney(1.23)).toBe("$1.23");
    expect(r.fmtMoney(36.9)).toBe("$36.90");
    expect(r.fmtMoney(0)).toBe("$0.00");
  });
});
