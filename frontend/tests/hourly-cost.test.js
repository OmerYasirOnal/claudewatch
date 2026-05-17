// Tests for the hourly-cost loader + summary helper + card-visibility gate.
// Mirrors the style of tests/forecast.test.js — app.js attaches the
// factory to globalThis so we don't have to refactor it into ES modules.
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import "../app.js";

function newRoot() {
  return globalThis.__claudewatchAppRoot();
}

describe("loadHourlyCost", () => {
  let r;
  beforeEach(() => {
    r = newRoot();
    r.hourlyCostData = null;
    r.hourlyCostError = null;
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("parses the /api/history/hourly-cost response into hourlyCostData", async () => {
    const payload = {
      hours: 168,
      bins: [
        { hour_start: "2026-05-10T00:00:00+00:00", cost_usd: 0.42, session_count: 3 },
        { hour_start: "2026-05-10T01:00:00+00:00", cost_usd: 0.0, session_count: 0 },
      ],
      total_cost_usd: 0.42,
    };
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => payload,
    });
    globalThis.fetch = fetchMock;

    await r.loadHourlyCost(168);

    expect(fetchMock).toHaveBeenCalledWith("/api/history/hourly-cost?hours=168");
    expect(r.hourlyCostData).toEqual({
      hours: 168,
      bins: payload.bins,
      total_cost_usd: 0.42,
    });
    expect(r.hourlyCostError).toBeNull();
  });

  it("defaults the hours argument to 168 (7d) when called with no args", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ hours: 168, bins: [], total_cost_usd: 0 }),
    });
    globalThis.fetch = fetchMock;
    await r.loadHourlyCost();
    expect(fetchMock).toHaveBeenCalledWith("/api/history/hourly-cost?hours=168");
  });

  it("handles empty bins gracefully (no throw, summary is degenerate)", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ hours: 168, bins: [], total_cost_usd: 0 }),
    });
    await r.loadHourlyCost(168);
    expect(r.hourlyCostData).toEqual({ hours: 168, bins: [], total_cost_usd: 0 });
    // hourlyCostSummary should be a string and contain the zero total.
    const s = r.hourlyCostSummary();
    expect(typeof s).toBe("string");
    expect(s).toContain("$0.00");
    expect(s).toContain("0 sessions");
  });

  it("records an error string on 500 and does NOT throw", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 });
    await expect(r.loadHourlyCost(168)).resolves.toBeUndefined();
    expect(r.hourlyCostData).toBeNull();
    expect(r.hourlyCostError).toContain("500");
  });

  it("swallows network errors and records an error string", async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    await r.loadHourlyCost(168);
    expect(r.hourlyCostData).toBeNull();
    expect(r.hourlyCostError).toMatch(/network/i);
  });
});

describe("hourlyCostSummary", () => {
  let r;
  beforeEach(() => { r = newRoot(); });

  it("formats total / hours / session count when populated", () => {
    r.hourlyCostData = {
      hours: 168,
      bins: [
        { hour_start: "2026-05-10T00:00:00+00:00", cost_usd: 1.5, session_count: 2 },
        { hour_start: "2026-05-10T01:00:00+00:00", cost_usd: 0.5, session_count: 1 },
      ],
      total_cost_usd: 2.0,
    };
    const s = r.hourlyCostSummary();
    expect(s).toContain("$2.00");
    expect(s).toContain("168 hours");
    expect(s).toContain("3 sessions");
  });

  it("returns an empty string when no data has been loaded yet", () => {
    r.hourlyCostData = null;
    expect(r.hourlyCostSummary()).toBe("");
  });
});

describe("hourly-cost card gating via showForecastCard()", () => {
  // The card reuses showForecastCard() — same gate as the forecast card.
  let r;
  beforeEach(() => { r = newRoot(); });

  it("is visible when plan==='api' and cardVisibility.cost is true", () => {
    r.config = { plan: "api" };
    r.cardVisibility = { ...r.cardVisibility, cost: true };
    expect(r.showForecastCard()).toBe(true);
  });

  it("is hidden when cardVisibility.cost is false", () => {
    r.config = { plan: "api" };
    r.cardVisibility = { ...r.cardVisibility, cost: false };
    expect(r.showForecastCard()).toBe(false);
  });

  it("is hidden when plan is not 'api' (Max/Pro users)", () => {
    r.config = { plan: "max" };
    r.cardVisibility = { ...r.cardVisibility, cost: true };
    expect(r.showForecastCard()).toBe(false);
  });
});
