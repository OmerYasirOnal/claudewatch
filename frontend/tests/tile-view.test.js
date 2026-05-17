// Tests for multi-session tile mode: tileMode state + localStorage,
// tileSessions() shape, refreshTilePreviews() fetching, stale-pid pruning,
// the dashboard-only timer wiring, and per-tile error isolation.
//
// NOTE: tileSessions() in app.js does NOT filter by location_type — it
// simply takes the top 6 from visibleSessions(). The task description hinted
// it might filter on `iterm`; we verified and assert against the actual
// behavior.
//
// Backend tile-preview endpoint is `/api/sessions/{pid}/log-tail?limit=6`
// (not `lines=N`). Asserting against the real shape.
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

function sess(over = {}) {
  return {
    pid: 1,
    status: "idle",
    is_in_flight: false,
    location_type: "iterm",
    cwd: "/repo",
    model: "claude-opus-4-7",
    last_activity_at: "2026-05-17T10:00:00Z",
    started_at: "",
    usage: { cost_estimate_usd: 0 },
    current_task_subject: null,
    tool_calls: { last_used: null },
    iterm_tab_title: "",
    ...over,
  };
}

beforeEach(() => {
  try { localStorage.clear(); } catch (e) { /* ignore */ }
});

describe("tileMode state + localStorage", () => {
  it("tileMode defaults to false", () => {
    const r = newRoot();
    expect(r.tileMode).toBe(false);
  });

  it("_loadLocalPrefs picks up persisted tileMode='1'", () => {
    localStorage.setItem("claudewatch.tileMode", "1");
    const r = newRoot();
    r._loadLocalPrefs();
    expect(r.tileMode).toBe(true);
  });

  it("_loadLocalPrefs picks up persisted tileMode='true' (legacy boolean)", () => {
    localStorage.setItem("claudewatch.tileMode", "true");
    const r = newRoot();
    r._loadLocalPrefs();
    expect(r.tileMode).toBe(true);
  });

  it("saveTileMode persists '1' when enabled and '0' when disabled", () => {
    const r = newRoot();
    r.tileMode = true;
    r.saveTileMode();
    expect(localStorage.getItem("claudewatch.tileMode")).toBe("1");

    r.tileMode = false;
    r.saveTileMode();
    expect(localStorage.getItem("claudewatch.tileMode")).toBe("0");
  });
});

describe("tileSessions() — top-N over visibleSessions()", () => {
  let r;
  beforeEach(() => {
    r = newRoot();
    r.filter = "All";
    r._searchQueryDebounced = "";
    r.bookmarks = [];
  });

  it("returns all visible sessions when there are ≤ 6", () => {
    r.sessions = [
      sess({ pid: 1, last_activity_at: "2026-05-17T01:00:00Z" }),
      sess({ pid: 2, last_activity_at: "2026-05-17T02:00:00Z" }),
      sess({ pid: 3, last_activity_at: "2026-05-17T03:00:00Z" }),
    ];
    expect(r.tileSessions().map((s) => s.pid).sort()).toEqual([1, 2, 3]);
  });

  it("caps at 6 (drops the lowest-activity tail)", () => {
    r.sessions = [];
    for (let i = 1; i <= 10; i++) {
      r.sessions.push(sess({ pid: i, last_activity_at: `2026-05-17T0${i % 10}:00:00Z` }));
    }
    const out = r.tileSessions();
    expect(out.length).toBe(6);
  });

  it("respects the active filter chip (tmux-only)", () => {
    r.sessions = [
      sess({ pid: 1, location_type: "iterm" }),
      sess({ pid: 2, location_type: "tmux" }),
      sess({ pid: 3, location_type: "tmux" }),
    ];
    r.filter = "Tmux";
    const out = r.tileSessions();
    expect(out.map((s) => s.pid).sort()).toEqual([2, 3]);
  });
});

describe("refreshTilePreviews() and _fetchTilePreview()", () => {
  let r;
  beforeEach(() => {
    r = newRoot();
    r.filter = "All";
    r._searchQueryDebounced = "";
    r.bookmarks = [];
  });
  afterEach(() => { vi.restoreAllMocks(); });

  it("fetches /api/sessions/{pid}/log-tail?limit=6 for every tile pid", async () => {
    r.sessions = [
      sess({ pid: 101, last_activity_at: "2026-05-17T02:00:00Z" }),
      sess({ pid: 202, last_activity_at: "2026-05-17T01:00:00Z" }),
    ];
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse({ entries: [{ text: "hi" }] }));

    await r.refreshTilePreviews();

    const urls = globalThis.fetch.mock.calls.map((c) => c[0]).sort();
    expect(urls).toEqual([
      "/api/sessions/101/log-tail?limit=6",
      "/api/sessions/202/log-tail?limit=6",
    ]);
  });

  it("caches preview entries indexed by pid", async () => {
    r.sessions = [sess({ pid: 7 })];
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse({ entries: [{ text: "line a" }] }));

    await r.refreshTilePreviews();

    expect(r.tilePreviews[7]).toEqual({ entries: [{ text: "line a" }], error: null });
    expect(r.tilePreviewEntries(7)).toEqual([{ text: "line a" }]);
  });

  it("tilePreviewEntries() returns [] when the pid has no cached preview", () => {
    expect(r.tilePreviewEntries(999)).toEqual([]);
  });

  it("accepts a bare array response (not wrapped in {entries})", async () => {
    r.sessions = [sess({ pid: 3 })];
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse([{ text: "one" }, { text: "two" }]));

    await r.refreshTilePreviews();

    expect(r.tilePreviewEntries(3)).toEqual([{ text: "one" }, { text: "two" }]);
  });

  it("a 500 on one tile records error=`HTTP 500` for that pid without breaking siblings", async () => {
    r.sessions = [sess({ pid: 1 }), sess({ pid: 2 })];
    globalThis.fetch = vi.fn().mockImplementation((url) => {
      if (String(url).includes("/sessions/2/")) {
        return Promise.resolve(jsonResponse({}, { ok: false, status: 500 }));
      }
      return Promise.resolve(jsonResponse({ entries: [{ text: "ok" }] }));
    });

    await r.refreshTilePreviews();

    expect(r.tilePreviews[1]).toEqual({ entries: [{ text: "ok" }], error: null });
    expect(r.tilePreviews[2].error).toMatch(/HTTP 500/);
    expect(r.tilePreviews[2].entries).toEqual([]);
  });

  it("network rejection records error='network' for that pid", async () => {
    r.sessions = [sess({ pid: 5 })];
    globalThis.fetch = vi.fn().mockRejectedValue(new Error("offline"));

    await r.refreshTilePreviews();

    expect(r.tilePreviews[5].error).toBe("network");
    expect(r.tilePreviews[5].entries).toEqual([]);
  });

  it("prunes stale pids from tilePreviews when they are no longer in tileSessions()", async () => {
    r.sessions = [sess({ pid: 1 }), sess({ pid: 2 })];
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse({ entries: [] }));

    await r.refreshTilePreviews();
    expect(Object.keys(r.tilePreviews).sort()).toEqual(["1", "2"]);

    // Pid 1 disappears (process died); pid 3 appears.
    r.sessions = [sess({ pid: 2 }), sess({ pid: 3 })];
    await r.refreshTilePreviews();

    expect(Object.keys(r.tilePreviews).sort()).toEqual(["2", "3"]);
  });
});

describe("_restartTileTimer() — dashboard + tileMode gating", () => {
  let r;
  beforeEach(() => {
    r = newRoot();
    r.filter = "All";
    r._searchQueryDebounced = "";
    r.bookmarks = [];
    r.sessions = [];
    vi.useFakeTimers();
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse({ entries: [] }));
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("does NOT start the timer when tileMode is off, even on the dashboard view", () => {
    r.view = "dashboard";
    r.tileMode = false;
    r._restartTileTimer();
    expect(r._tileTimer).toBeNull();
  });

  it("does NOT start the timer when on another view, even with tileMode on", () => {
    r.view = "files";
    r.tileMode = true;
    r._restartTileTimer();
    expect(r._tileTimer).toBeNull();
  });

  it("starts a 5s polling timer when on dashboard + tileMode on, and clears it when leaving", () => {
    r.view = "dashboard";
    r.tileMode = true;
    r._restartTileTimer();
    expect(r._tileTimer).not.toBeNull();

    // Switch off tileMode → timer should clear.
    r.tileMode = false;
    r._restartTileTimer();
    expect(r._tileTimer).toBeNull();
  });
});
