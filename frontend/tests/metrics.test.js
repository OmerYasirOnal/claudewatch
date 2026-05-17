// Tests for loadMetrics() — fetches /api/metrics and stores the payload on
// metricsData (or metricsError on failure). Mirrors the stubbing pattern in
// status-tab.test.js: globalThis.fetch is replaced per test, never reaches
// the network.
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

describe("loadMetrics", () => {
  let r;
  beforeEach(() => { r = newRoot(); });
  afterEach(() => { vi.restoreAllMocks(); });

  it("parses /api/metrics into metricsData and clears any prior error", async () => {
    const payload = {
      scheduler_ticks_total: 100,
      scheduler_tick_duration_ms_sum: 250.0,
      scheduler_tick_duration_ms_max: 12.3,
      scheduler_tick_duration_ms_avg: 2.5,
      iterm_refresh_total: 40,
      iterm_refresh_duration_ms_sum: 80.0,
      iterm_refresh_duration_ms_avg: 2.0,
      iterm_refresh_failures_total: 1,
      broadcasts_total: 33,
      sse_subscribers: 2,
      detector_failures_total: 0,
      process_scan_last_count: 5,
      started_at: "2026-05-17T12:00:00Z",
      uptime_seconds: 600,
    };
    r.metricsError = "stale error";
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse(payload));

    await r.loadMetrics();

    expect(globalThis.fetch).toHaveBeenCalledWith("/api/metrics");
    expect(r.metricsData).toEqual(payload);
    expect(r.metricsError).toBeNull();
  });

  it("sets metricsError on a 500 response and leaves metricsData unchanged", async () => {
    r.metricsData = { uptime_seconds: 42 };
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse({}, { ok: false, status: 500 }));

    await r.loadMetrics();

    expect(r.metricsData).toEqual({ uptime_seconds: 42 });
    expect(r.metricsError).toMatch(/HTTP 500/);
    expect(r.metricsError).toMatch(/\/api\/metrics/);
  });

  it("sets metricsError to a network message when fetch rejects", async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error("boom"));

    await r.loadMetrics();

    expect(r.metricsError).toBe("Network error loading metrics");
  });

  it("surfaces the actual status code in the error message on 404", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse({}, { ok: false, status: 404 }));

    await r.loadMetrics();

    expect(r.metricsError).toMatch(/HTTP 404/);
  });

  it("clears a stale network error when a later call succeeds", async () => {
    // First call: error.
    globalThis.fetch = vi.fn().mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce(jsonResponse({ uptime_seconds: 1 }));

    await r.loadMetrics();
    expect(r.metricsError).toBe("Network error loading metrics");

    await r.loadMetrics();
    expect(r.metricsError).toBeNull();
    expect(r.metricsData).toEqual({ uptime_seconds: 1 });
  });
});

describe("_startAdminPolling", () => {
  let r;
  beforeEach(() => { r = newRoot(); });
  afterEach(() => {
    if (r._adminPollTimer) clearInterval(r._adminPollTimer);
    vi.restoreAllMocks();
  });

  it("triggers an immediate /api/metrics fetch alongside status + logs", async () => {
    const calls = [];
    globalThis.fetch = vi.fn().mockImplementation((url) => {
      calls.push(String(url));
      if (String(url).startsWith("/api/admin/logs")) {
        return Promise.resolve(jsonResponse({ lines: [] }));
      }
      return Promise.resolve(jsonResponse({}));
    });

    r._startAdminPolling();
    // Yield to the microtask queue so the three loaders' awaits resolve.
    await new Promise((res) => setTimeout(res, 0));

    expect(calls).toContain("/api/admin/status");
    expect(calls).toContain("/api/metrics");
    expect(calls.some((u) => u.startsWith("/api/admin/logs"))).toBe(true);
  });
});
