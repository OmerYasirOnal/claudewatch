// Tests for the Status tab data loaders: /api/admin/status, /api/admin/logs,
// and the pruneNow() flow. We stub globalThis.fetch (and confirm/alert) so
// nothing touches the real backend.
import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import "../app.js";

function newRoot() {
  return globalThis.__claudewatchAppRoot();
}

// jsdom doesn't define $nextTick — the loaders guard with `this.$nextTick && ...`
// so this is harmless to omit.

function jsonResponse(body, init = {}) {
  return {
    ok: init.ok ?? true,
    status: init.status ?? 200,
    json: async () => body,
  };
}

describe("loadAdminStatus", () => {
  let r;
  beforeEach(() => { r = newRoot(); });
  afterEach(() => { vi.restoreAllMocks(); });

  it("parses /api/admin/status into adminStatus and clears error", async () => {
    const payload = {
      uptime_seconds: 1234,
      sessions_active: 2,
      sessions_today: 7,
      log_path: "/tmp/claudewatch.log",
    };
    r.adminStatusError = "stale error";
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse(payload));

    await r.loadAdminStatus();

    expect(globalThis.fetch).toHaveBeenCalledWith("/api/admin/status");
    expect(r.adminStatus).toEqual(payload);
    expect(r.adminStatusError).toBeNull();
  });

  it("sets adminStatusError on non-200 HTTP response", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse({}, { ok: false, status: 500 }));

    await r.loadAdminStatus();

    expect(r.adminStatus).toBeNull(); // unchanged
    expect(r.adminStatusError).toMatch(/HTTP 500/);
    expect(r.adminStatusError).toMatch(/\/api\/admin\/status/);
  });

  it("sets adminStatusError on 404 with the actual status in the message", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse({}, { ok: false, status: 404 }));

    await r.loadAdminStatus();

    expect(r.adminStatusError).toMatch(/HTTP 404/);
  });

  it("falls back to 'Network error loading status' when fetch rejects", async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error("boom"));

    await r.loadAdminStatus();

    expect(r.adminStatusError).toBe("Network error loading status");
  });
});

describe("loadAdminLogs", () => {
  let r;
  beforeEach(() => { r = newRoot(); });
  afterEach(() => { vi.restoreAllMocks(); });

  it("parses /api/admin/logs into adminLogs and clears error", async () => {
    const payload = {
      lines: ["line a", "line b"],
      path: "/var/log/claudewatch.log",
      size_bytes: 4096,
      truncated: false,
    };
    r.adminLogsError = "stale error";
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse(payload));

    await r.loadAdminLogs();

    // Lines count comes from adminLogsLineCount (default 200).
    expect(globalThis.fetch).toHaveBeenCalledTimes(1);
    const url = globalThis.fetch.mock.calls[0][0];
    expect(url).toMatch(/^\/api\/admin\/logs\?/);
    expect(url).toContain("lines=200");
    expect(r.adminLogs).toEqual(payload);
    expect(r.adminLogsError).toBeNull();
    expect(r.adminLogsLoading).toBe(false);
  });

  it("respects adminLogsLineCount and the grep filter", async () => {
    r.adminLogsLineCount = 50;
    r.adminLogsGrep = "ERROR";
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse({ lines: [] }));

    await r.loadAdminLogs();

    const url = globalThis.fetch.mock.calls[0][0];
    expect(url).toContain("lines=50");
    expect(url).toMatch(/grep=ERROR/);
  });

  it("defaults lines=200 when adminLogsLineCount is falsy", async () => {
    r.adminLogsLineCount = 0;
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse({ lines: [] }));

    await r.loadAdminLogs();

    expect(globalThis.fetch.mock.calls[0][0]).toContain("lines=200");
  });

  it("sets adminLogsError on non-200 and clears the loading flag", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse({}, { ok: false, status: 500 }));

    await r.loadAdminLogs();

    expect(r.adminLogsError).toMatch(/HTTP 500/);
    expect(r.adminLogsError).toMatch(/\/api\/admin\/logs/);
    expect(r.adminLogsLoading).toBe(false);
  });

  it("sets a user-visible error for a 404", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse({}, { ok: false, status: 404 }));

    await r.loadAdminLogs();

    expect(r.adminLogsError).toMatch(/HTTP 404/);
    expect(r.adminLogsLoading).toBe(false);
  });

  it("falls back to 'Network error loading logs' when fetch rejects", async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error("connection refused"));

    await r.loadAdminLogs();

    expect(r.adminLogsError).toBe("Network error loading logs");
    expect(r.adminLogsLoading).toBe(false);
  });
});

describe("pruneNow", () => {
  let r;
  beforeEach(() => { r = newRoot(); });
  afterEach(() => { vi.restoreAllMocks(); });

  it("aborts when confirm() returns false and never touches the network", async () => {
    globalThis.fetch = vi.fn();
    globalThis.confirm = vi.fn().mockReturnValue(false);

    await r.pruneNow(48);

    expect(globalThis.confirm).toHaveBeenCalled();
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it("POSTs to /api/admin/prune?hours=H and refreshes status + history", async () => {
    globalThis.confirm = vi.fn().mockReturnValue(true);
    const calls = [];
    globalThis.fetch = vi.fn().mockImplementation((url, init) => {
      calls.push({ url, init });
      if (typeof url === "string" && url.startsWith("/api/admin/prune")) {
        return Promise.resolve(jsonResponse({ rows_deleted: 5 }));
      }
      if (url === "/api/admin/status") {
        return Promise.resolve(jsonResponse({ uptime_seconds: 1 }));
      }
      if (url === "/api/history") {
        return Promise.resolve(jsonResponse([]));
      }
      if (typeof url === "string" && url.startsWith("/api/history/hourly")) {
        return Promise.resolve(jsonResponse({ bins: [] }));
      }
      return Promise.resolve(jsonResponse({}));
    });

    await r.pruneNow(72);

    const urls = calls.map((c) => c.url);
    expect(urls).toContain("/api/admin/prune?hours=72");
    const pruneCall = calls.find((c) => String(c.url).startsWith("/api/admin/prune"));
    expect(pruneCall.init).toEqual({ method: "POST" });

    // pruneNow then calls loadAdminStatus + loadHistory + loadHourlyHistory(24).
    expect(urls).toContain("/api/admin/status");
    expect(urls).toContain("/api/history");
    expect(urls.some((u) => String(u).startsWith("/api/history/hourly?hours=24"))).toBe(true);

    // Toast set on success.
    expect(r._adminPostToast).toMatch(/Pruned 5/);
  });

  it("defaults hours to 48 when the arg is missing", async () => {
    globalThis.confirm = vi.fn().mockReturnValue(true);
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse({ rows_deleted: 0 }));

    await r.pruneNow();

    const firstUrl = globalThis.fetch.mock.calls[0][0];
    expect(firstUrl).toBe("/api/admin/prune?hours=48");
  });

  it("alerts and bails when the prune endpoint returns non-200", async () => {
    globalThis.confirm = vi.fn().mockReturnValue(true);
    const alertSpy = vi.fn();
    globalThis.alert = alertSpy;
    globalThis.fetch = vi.fn().mockResolvedValue(
      jsonResponse({ detail: "db locked" }, { ok: false, status: 500 }),
    );

    await r.pruneNow(48);

    expect(alertSpy).toHaveBeenCalled();
    const msg = alertSpy.mock.calls[0][0];
    expect(msg).toMatch(/HTTP 500/);
    expect(msg).toMatch(/db locked/);
    // Status etc. are NOT refreshed when the prune itself failed.
    expect(globalThis.fetch).toHaveBeenCalledTimes(1);
  });

  it("alerts on a network error and bails", async () => {
    globalThis.confirm = vi.fn().mockReturnValue(true);
    const alertSpy = vi.fn();
    globalThis.alert = alertSpy;
    globalThis.fetch = vi.fn().mockRejectedValue(new Error("offline"));

    await r.pruneNow(48);

    expect(alertSpy).toHaveBeenCalled();
    expect(String(alertSpy.mock.calls[0][0])).toMatch(/Prune failed/);
  });
});

// NOTE: There is no top-level `loadStatus()` function — the Status tab uses
// loadAdminStatus + loadAdminLogs, polled by _startAdminPolling.
