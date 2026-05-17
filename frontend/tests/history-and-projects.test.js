// Tests for the History panel + Projects/Insights loaders and the
// visibleHistory() filter helper. All network calls are stubbed.
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

describe("loadHistory", () => {
  let r;
  beforeEach(() => { r = newRoot(); });
  afterEach(() => { vi.restoreAllMocks(); });

  it("parses /api/history into the history array", async () => {
    const payload = [
      { pid: 1, ended_at: "2026-05-17T10:00:00Z", cost_estimate: 0.5 },
      { pid: 2, ended_at: "2026-05-17T12:00:00Z", cost_estimate: 1.2 },
    ];
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse(payload));

    await r.loadHistory();

    expect(globalThis.fetch).toHaveBeenCalledWith("/api/history");
    expect(r.history).toEqual(payload);
  });

  it("empty response stays an empty array, no error", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse([]));

    await r.loadHistory();

    expect(r.history).toEqual([]);
    expect(r.errorBanner).toBeNull();
  });

  it("sets the error banner on a 500", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse({}, { ok: false, status: 500 }));

    await r.loadHistory();

    expect(r.errorBanner).not.toBeNull();
    expect(r.errorBanner.text).toMatch(/HTTP 500/);
    expect(r.errorBanner.text).toMatch(/\/api\/history/);
  });

  it("sets the error banner on a network error", async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error("offline"));

    await r.loadHistory();

    expect(r.errorBanner).not.toBeNull();
    expect(r.errorBanner.text).toMatch(/Failed to load \/api\/history/);
  });
});

describe("visibleHistory filter", () => {
  let r;
  beforeEach(() => { r = newRoot(); });

  function row(over = {}) {
    return { pid: 1, ended_at: null, cost_estimate: 0, ...over };
  }

  it("filter='all' returns the array as-is", () => {
    r.history = [row({ pid: 1 }), row({ pid: 2 })];
    r.historyFilter = "all";
    expect(r.visibleHistory().map((h) => h.pid)).toEqual([1, 2]);
  });

  it("filter='today' keeps rows ended within the last 24h", () => {
    const now = Date.now();
    r.history = [
      row({ pid: 1, ended_at: new Date(now - 60_000).toISOString() }),     // 1m ago — keep
      row({ pid: 2, ended_at: new Date(now - 48 * 3600_000).toISOString() }), // 2d ago — drop
      row({ pid: 3, ended_at: null }),                                       // no ts — drop
    ];
    r.historyFilter = "today";
    expect(r.visibleHistory().map((h) => h.pid)).toEqual([1]);
  });

  it("filter='week' keeps rows ended within the last 7 days", () => {
    const now = Date.now();
    r.history = [
      row({ pid: 1, ended_at: new Date(now - 3 * 24 * 3600_000).toISOString() }), // 3d ago — keep
      row({ pid: 2, ended_at: new Date(now - 14 * 24 * 3600_000).toISOString() }), // 14d ago — drop
    ];
    r.historyFilter = "week";
    expect(r.visibleHistory().map((h) => h.pid)).toEqual([1]);
  });

  it("filter='high-cost' keeps rows with cost_estimate >= 1.0", () => {
    r.history = [
      row({ pid: 1, cost_estimate: 0.5 }),
      row({ pid: 2, cost_estimate: 1.0 }),
      row({ pid: 3, cost_estimate: 2.7 }),
    ];
    r.historyFilter = "high-cost";
    expect(r.visibleHistory().map((h) => h.pid).sort()).toEqual([2, 3]);
  });

  it("returns [] when this.history isn't an array", () => {
    r.history = null;
    r.historyFilter = "all";
    expect(r.visibleHistory()).toEqual([]);
  });
});

describe("history row expand helpers", () => {
  let r;
  beforeEach(() => { r = newRoot(); });

  it("historyKey is pid|started_at", () => {
    expect(r.historyKey({ pid: 7, started_at: "2026-05-17T00:00:00Z" }))
      .toBe("7|2026-05-17T00:00:00Z");
    expect(r.historyKey({ pid: 7 })).toBe("7|");
    expect(r.historyKey(null)).toBe("");
  });

  it("toggleHistoryRow flips expanded state and isHistoryRowExpanded reads it", () => {
    const h = { pid: 1, started_at: "2026-01-01" };
    expect(r.isHistoryRowExpanded(h)).toBe(false);
    r.toggleHistoryRow(h);
    expect(r.isHistoryRowExpanded(h)).toBe(true);
    r.toggleHistoryRow(h);
    expect(r.isHistoryRowExpanded(h)).toBe(false);
  });

  it("historySummaryJson stringifies summary preferentially", () => {
    const out = r.historySummaryJson({ pid: 1, summary: { foo: 1 } });
    expect(JSON.parse(out)).toEqual({ foo: 1 });
  });

  it("historySummaryJson falls back to the full row when there's no summary", () => {
    const out = r.historySummaryJson({ pid: 1, ended_at: "x" });
    expect(JSON.parse(out)).toEqual({ pid: 1, ended_at: "x" });
  });
});

describe("loadHourlyHistory", () => {
  let r;
  beforeEach(() => { r = newRoot(); });
  afterEach(() => { vi.restoreAllMocks(); });

  it("parses /api/history/hourly?hours=N into hourlyHistory", async () => {
    const payload = { bins: [
      { hour: "2026-05-17T10:00:00Z", sessions_started: 3, cost: 0.5 },
      { hour: "2026-05-17T11:00:00Z", sessions_started: 1, cost: 0.1 },
    ]};
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse(payload));

    await r.loadHourlyHistory(24);

    expect(globalThis.fetch).toHaveBeenCalledWith("/api/history/hourly?hours=24");
    expect(r.hourlyHistory).toEqual(payload);
  });

  it("defaults hours to 24 when argument is falsy", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse({ bins: [] }));

    await r.loadHourlyHistory();

    expect(globalThis.fetch).toHaveBeenCalledWith("/api/history/hourly?hours=24");
  });

  it("coerces strings via Number()", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse({ bins: [] }));

    await r.loadHourlyHistory("48");

    expect(globalThis.fetch).toHaveBeenCalledWith("/api/history/hourly?hours=48");
  });

  it("response without bins normalizes to { bins: [] }", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse({}));

    await r.loadHourlyHistory(24);

    expect(r.hourlyHistory).toEqual({ bins: [] });
  });

  it("silently ignores network errors (no error banner — chart shows 'No data')", async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    r.hourlyHistory = { bins: [{ hour: "2026-05-17T10:00:00Z" }] };

    await r.loadHourlyHistory(24);

    // No error banner is set — the silent-degrade is part of the loader.
    expect(r.errorBanner).toBeNull();
    // Existing data is unchanged.
    expect(r.hourlyHistory.bins.length).toBe(1);
  });

  it("ignores non-200 responses silently as well", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse({}, { ok: false, status: 500 }));

    await r.loadHourlyHistory(24);

    expect(r.errorBanner).toBeNull();
  });
});

describe("loadInsights (projects + hourly)", () => {
  let r;
  beforeEach(() => { r = newRoot(); });
  afterEach(() => { vi.restoreAllMocks(); });

  it("loads /api/projects + /api/history/hourly and stores them in insightsData", async () => {
    const projects = [
      { cwd: "/repo/alpha", session_count: 4 },
      { cwd: "/repo/beta", session_count: 1 },
    ];
    const hourly = { bins: [{ hour: "2026-05-17T10:00:00Z", cost: 0.25 }] };
    globalThis.fetch = vi.fn().mockImplementation((url) => {
      if (url === "/api/projects") return Promise.resolve(jsonResponse(projects));
      if (url === "/api/history/hourly?hours=24") return Promise.resolve(jsonResponse(hourly));
      return Promise.resolve(jsonResponse({}));
    });

    await r.loadInsights();

    expect(r.insightsData.projects).toEqual(projects);
    expect(r.insightsData.hourly).toEqual(hourly);
  });

  it("falls back to [] / { bins: [] } when either endpoint 500s", async () => {
    globalThis.fetch = vi.fn().mockImplementation((url) => {
      if (url === "/api/projects") {
        return Promise.resolve(jsonResponse({}, { ok: false, status: 500 }));
      }
      return Promise.resolve(jsonResponse({}, { ok: false, status: 500 }));
    });

    await r.loadInsights();

    expect(r.insightsData.projects).toEqual([]);
    expect(r.insightsData.hourly).toEqual({ bins: [] });
  });

  it("coerces a non-array projects payload to []", async () => {
    globalThis.fetch = vi.fn().mockImplementation((url) => {
      if (url === "/api/projects") return Promise.resolve(jsonResponse({ oops: true }));
      return Promise.resolve(jsonResponse({ bins: [] }));
    });

    await r.loadInsights();

    expect(r.insightsData.projects).toEqual([]);
  });

  it("survives both endpoints rejecting", async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error("offline"));

    await r.loadInsights();

    // Promise.all is wrapped in .catch(() => null) inline, so neither rejection bubbles.
    // The end state is the safe defaults.
    expect(r.insightsData.projects).toEqual([]);
    expect(r.insightsData.hourly).toEqual({ bins: [] });
  });
});

// NOTE: There is no `loadEndedSessions()` or `loadProjects()` in app.js
// — history rows come from /api/history via loadHistory(), and projects
// are fetched as part of loadInsights().
