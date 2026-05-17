// Tests for the Insights tab loaders and the view-change side effects.
// These tests complement the existing loadInsights() coverage in
// history-and-projects.test.js by focusing on:
//   - the Promise.all() URL fanout (projects + hourly)
//   - the empty-state defaults
//   - what happens when _onViewChange("insights") fires (loads + 30s timer)
//   - _renderInsightsCharts() resilience when no canvas exists
//
// Note: loadInsights() does NOT expose an `insightsError` field — when an
// endpoint fails it silently degrades to safe defaults. We assert against
// that actual behavior, not the spec.
import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import "../app.js";

function newRoot() {
  return globalThis.__claudewatchAppRoot();
}

function jsonResponse(body, init = {}) {
  return {
    ok: init.ok ?? true,
    status: init.status ?? 200,
    json: async () => body,
  };
}

describe("loadInsights — endpoint fanout", () => {
  let r;
  beforeEach(() => { r = newRoot(); });
  afterEach(() => { vi.restoreAllMocks(); });

  it("fires both /api/projects and /api/history/hourly?hours=24 in parallel", async () => {
    const calls = [];
    globalThis.fetch = vi.fn().mockImplementation((url) => {
      calls.push(url);
      if (url === "/api/projects") return Promise.resolve(jsonResponse([]));
      if (url === "/api/history/hourly?hours=24") return Promise.resolve(jsonResponse({ bins: [] }));
      return Promise.resolve(jsonResponse({}));
    });

    await r.loadInsights();

    expect(calls).toContain("/api/projects");
    expect(calls).toContain("/api/history/hourly?hours=24");
    expect(globalThis.fetch).toHaveBeenCalledTimes(2);
  });

  it("empty arrays/objects produce empty state without throwing", async () => {
    globalThis.fetch = vi.fn().mockImplementation((url) => {
      if (url === "/api/projects") return Promise.resolve(jsonResponse([]));
      return Promise.resolve(jsonResponse({ bins: [] }));
    });

    await r.loadInsights();

    expect(r.insightsData.projects).toEqual([]);
    expect(r.insightsData.hourly).toEqual({ bins: [] });
  });

  it("a single endpoint failing (404) does not throw or wipe the other side", async () => {
    const projects = [{ cwd: "/repo/a", session_count: 1 }];
    globalThis.fetch = vi.fn().mockImplementation((url) => {
      if (url === "/api/projects") return Promise.resolve(jsonResponse(projects));
      return Promise.resolve(jsonResponse({}, { ok: false, status: 404 }));
    });

    await r.loadInsights();

    expect(r.insightsData.projects).toEqual(projects);
    // Hourly failed → safe default.
    expect(r.insightsData.hourly).toEqual({ bins: [] });
  });

  it("a 500 on the projects endpoint degrades projects to [] but keeps hourly", async () => {
    const hourly = { bins: [{ hour: "2026-05-17T10:00:00Z", cost: 1.2 }] };
    globalThis.fetch = vi.fn().mockImplementation((url) => {
      if (url === "/api/projects") return Promise.resolve(jsonResponse({}, { ok: false, status: 500 }));
      return Promise.resolve(jsonResponse(hourly));
    });

    await r.loadInsights();

    expect(r.insightsData.projects).toEqual([]);
    expect(r.insightsData.hourly).toEqual(hourly);
  });

  it("normalizes a hourly response missing the bins field to { bins: [] }", async () => {
    globalThis.fetch = vi.fn().mockImplementation((url) => {
      if (url === "/api/projects") return Promise.resolve(jsonResponse([]));
      // Missing bins entirely.
      return Promise.resolve(jsonResponse({ ok: 1 }));
    });

    await r.loadInsights();

    expect(r.insightsData.hourly).toEqual({ bins: [] });
  });
});

describe("_onViewChange('insights') wiring", () => {
  let r;
  beforeEach(() => {
    r = newRoot();
    vi.useFakeTimers();
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse([]));
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("entering the insights view triggers an immediate loadInsights + loadForecast + loadHourlyCost(168)", () => {
    r.view = "insights";
    r._onViewChange("insights");

    const urls = globalThis.fetch.mock.calls.map((c) => c[0]);
    // loadInsights fans out to two endpoints; loadForecast and loadHourlyCost each fire one.
    expect(urls).toContain("/api/projects");
    expect(urls).toContain("/api/history/hourly?hours=24");
    expect(urls.some((u) => String(u).startsWith("/api/forecast"))).toBe(true);
    expect(urls.some((u) => String(u).startsWith("/api/history/hourly-cost"))).toBe(true);
  });

  it("entering insights starts a 30s polling timer; leaving clears it", () => {
    expect(r._insightsTimer).toBeNull();

    r.view = "insights";
    r._onViewChange("insights");
    expect(r._insightsTimer).not.toBeNull();

    // Reset call counts; advance one polling tick.
    globalThis.fetch.mockClear();
    vi.advanceTimersByTime(30_000);
    const urls = globalThis.fetch.mock.calls.map((c) => c[0]);
    expect(urls).toContain("/api/projects");

    // Leave the tab.
    r.view = "dashboard";
    r._onViewChange("dashboard");
    expect(r._insightsTimer).toBeNull();

    // Further timer ticks should not refetch.
    globalThis.fetch.mockClear();
    vi.advanceTimersByTime(60_000);
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it("re-entering insights clears the previous timer (no leak / double-poll)", () => {
    r.view = "insights";
    r._onViewChange("insights");
    const first = r._insightsTimer;
    expect(first).not.toBeNull();

    r._onViewChange("insights");
    const second = r._insightsTimer;
    expect(second).not.toBeNull();
    expect(second).not.toBe(first);
  });
});

// #151: cost loaders + dedicated timer should only run when plan === "api".
// Non-API plans (Max/Pro) skip both the immediate fetch and the periodic
// timer entirely, sparing 3 SQLite hits per poll on the daemon.
describe("_costInsightsTimer plan-gating (#151)", () => {
  let r;
  beforeEach(() => {
    r = newRoot();
    vi.useFakeTimers();
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse([]));
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  function costUrls(calls) {
    return calls
      .map((c) => String(c[0]))
      .filter((u) => u.startsWith("/api/forecast") || u.startsWith("/api/history/hourly-cost") || u === "/api/budgets");
  }

  it("entering insights on API plan schedules _costInsightsTimer + fires cost loaders", () => {
    r.config = { plan: "api" };
    r.view = "insights";
    r._onViewChange("insights");

    expect(r._costInsightsTimer).not.toBeNull();
    // Immediate fan-out to all three cost endpoints.
    const urls = costUrls(globalThis.fetch.mock.calls);
    expect(urls.some((u) => u.startsWith("/api/forecast"))).toBe(true);
    expect(urls.some((u) => u.startsWith("/api/history/hourly-cost"))).toBe(true);
    expect(urls).toContain("/api/budgets");

    // Periodic tick refetches the cost endpoints.
    globalThis.fetch.mockClear();
    vi.advanceTimersByTime(30_000);
    const tick = costUrls(globalThis.fetch.mock.calls);
    expect(tick.length).toBeGreaterThanOrEqual(3);
  });

  it("entering insights on a non-API plan does NOT schedule _costInsightsTimer", () => {
    r.config = { plan: "max" };
    r.view = "insights";
    r._onViewChange("insights");

    expect(r._costInsightsTimer).toBeNull();
    // No cost endpoints hit either.
    expect(costUrls(globalThis.fetch.mock.calls)).toEqual([]);

    // Even after multiple polling intervals, no cost endpoints should fire.
    globalThis.fetch.mockClear();
    vi.advanceTimersByTime(120_000);
    expect(costUrls(globalThis.fetch.mock.calls)).toEqual([]);
  });

  it("loadInsights (plan-agnostic) still runs on a non-API plan", () => {
    r.config = { plan: "max" };
    r.view = "insights";
    r._onViewChange("insights");

    const urls = globalThis.fetch.mock.calls.map((c) => String(c[0]));
    // /api/projects + /api/history/hourly are plan-agnostic — still scheduled.
    expect(urls).toContain("/api/projects");
    expect(urls).toContain("/api/history/hourly?hours=24");
    expect(r._insightsTimer).not.toBeNull();
  });

  it("leaving the insights tab clears both timers", () => {
    r.config = { plan: "api" };
    r.view = "insights";
    r._onViewChange("insights");
    expect(r._insightsTimer).not.toBeNull();
    expect(r._costInsightsTimer).not.toBeNull();

    r.view = "dashboard";
    r._onViewChange("dashboard");
    expect(r._insightsTimer).toBeNull();
    expect(r._costInsightsTimer).toBeNull();
  });

  it("flipping plan max→api via saveConfigDraft (re)starts the cost timer", async () => {
    // Start on Max, on the insights tab — no cost timer.
    r.config = { plan: "max", notifications: {}, remote_control: { enabled: false }, editor: { enabled: false, command: "code" }, budgets: { enabled: false, daily_usd: 5, weekly_usd: 30, monthly_usd: 100, warn_at_percent: 80 }, pricing: {} };
    r._syncConfigDraft();
    r.view = "insights";
    r._onViewChange("insights");
    expect(r._costInsightsTimer).toBeNull();

    // User flips plan to api in the Settings tab and saves.
    r.configDraft.plan = "api";
    r.markConfigDirty();
    const saved = JSON.parse(JSON.stringify(r.configDraft));
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse(saved));

    await r.saveConfigDraft();

    expect(r.config.plan).toBe("api");
    expect(r._costInsightsTimer).not.toBeNull();
  });

  it("flipping plan api→max via saveConfigDraft tears down the cost timer", async () => {
    r.config = { plan: "api", notifications: {}, remote_control: { enabled: false }, editor: { enabled: false, command: "code" }, budgets: { enabled: false, daily_usd: 5, weekly_usd: 30, monthly_usd: 100, warn_at_percent: 80 }, pricing: {} };
    r._syncConfigDraft();
    r.view = "insights";
    r._onViewChange("insights");
    expect(r._costInsightsTimer).not.toBeNull();

    r.configDraft.plan = "max";
    r.markConfigDirty();
    const saved = JSON.parse(JSON.stringify(r.configDraft));
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse(saved));

    await r.saveConfigDraft();

    expect(r.config.plan).toBe("max");
    expect(r._costInsightsTimer).toBeNull();
  });
});

// #151: loaders themselves also have an inner showCost() guard so even if
// they are invoked directly (init prime, future call site), they no-op on
// non-API plans. Belt-and-suspenders against the daemon getting 3 spurious
// SQLite hits.
describe("cost loaders no-op on non-API plans (#151)", () => {
  let r;
  beforeEach(() => { r = newRoot(); });
  afterEach(() => { vi.restoreAllMocks(); });

  it("loadForecast skips fetch when plan != 'api'", async () => {
    r.config = { plan: "max" };
    globalThis.fetch = vi.fn();
    await r.loadForecast();
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it("loadHourlyCost skips fetch when plan != 'api'", async () => {
    r.config = { plan: "max" };
    globalThis.fetch = vi.fn();
    await r.loadHourlyCost(168);
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it("loadBudgets skips fetch when plan != 'api'", async () => {
    r.config = { plan: "max" };
    globalThis.fetch = vi.fn();
    await r.loadBudgets();
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it("all three loaders DO fetch when plan === 'api'", async () => {
    r.config = { plan: "api" };
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse({}));
    await r.loadForecast();
    await r.loadHourlyCost(168);
    await r.loadBudgets();
    expect(globalThis.fetch).toHaveBeenCalledTimes(3);
  });
});

describe("_renderInsightsCharts", () => {
  let r;
  beforeEach(() => {
    r = newRoot();
    // Make sure no #insights-bar/#insights-donut canvases exist in the DOM.
    document.body.innerHTML = "";
  });
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("is a no-op (and does not throw) when no insights canvases are present", () => {
    // Defaults are an empty data shape — should not blow up.
    expect(() => r._renderInsightsCharts()).not.toThrow();
  });

  it("tolerates being called with an empty insightsData payload", () => {
    r.insightsData = { projects: [], hourly: { bins: [] } };
    expect(() => r._renderInsightsCharts()).not.toThrow();
  });
});
