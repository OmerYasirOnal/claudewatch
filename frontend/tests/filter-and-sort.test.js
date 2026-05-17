// Tests for visibleSessions(): filter chips, search-query substring
// matches, and bookmark-first / most-recently-active sorting.
import { describe, it, expect, beforeEach } from "vitest";
import "../app.js";

function newRoot() {
  return globalThis.__claudewatchAppRoot();
}

function sess(over = {}) {
  return {
    pid: 1,
    status: "idle",
    is_in_flight: false,
    location_type: "iterm",
    cwd: "/some/path",
    model: "claude-opus-4-7",
    last_activity_at: "2026-05-17T10:00:00Z",
    started_at: "",
    usage: { cost_estimate_usd: 0 },
    current_task_subject: null,
    current_task_active_form: null,
    tool_calls: { last_used: null },
    iterm_tab_title: "",
    ...over,
  };
}

describe("visibleSessions filter chips", () => {
  let r;
  beforeEach(() => {
    r = newRoot();
    r.sessions = [
      sess({
        pid: 1, location_type: "iterm", status: "working",
        cwd: "/repo/alpha", model: "claude-opus-4-7",
        last_activity_at: "2026-05-17T10:00:00Z",
        usage: { cost_estimate_usd: 0.5 },
        current_task_subject: "building feature",
        tool_calls: { last_used: "Bash" },
      }),
      sess({
        pid: 2, location_type: "tmux", status: "idle",
        cwd: "/repo/beta", model: "claude-haiku-4-5",
        last_activity_at: "2026-05-17T11:00:00Z",
        usage: { cost_estimate_usd: 2.5 },
      }),
      sess({
        pid: 3, location_type: "headless", status: "working",
        is_in_flight: true,
        cwd: "/repo/gamma",
        last_activity_at: "2026-05-17T09:00:00Z",
        usage: { cost_estimate_usd: 0.01 },
      }),
    ];
    r.bookmarks = [];
    r._searchQueryDebounced = "";
  });

  it("filter=All returns all, newest-activity first", () => {
    r.filter = "All";
    const out = r.visibleSessions();
    expect(out.map((s) => s.pid)).toEqual([2, 1, 3]);
  });

  it("filter=iTerm returns only iterm sessions", () => {
    r.filter = "iTerm";
    const out = r.visibleSessions();
    expect(out.length).toBe(1);
    expect(out[0].pid).toBe(1);
  });

  it("filter=Tmux returns only tmux sessions", () => {
    r.filter = "Tmux";
    const out = r.visibleSessions();
    expect(out.length).toBe(1);
    expect(out[0].pid).toBe(2);
  });

  it("filter=Headless returns only headless sessions", () => {
    r.filter = "Headless";
    const out = r.visibleSessions();
    expect(out.length).toBe(1);
    expect(out[0].pid).toBe(3);
  });

  it("filter=Working includes status=working OR is_in_flight", () => {
    r.filter = "Working";
    const out = r.visibleSessions();
    expect(out.map((s) => s.pid).sort()).toEqual([1, 3]);
  });

  it("filter=Idle excludes in-flight even if status=idle", () => {
    r.filter = "Idle";
    const out = r.visibleSessions();
    expect(out.length).toBe(1);
    expect(out[0].pid).toBe(2);
  });

  it("filter=High-cost returns only cost_estimate_usd ≥ 1", () => {
    r.filter = "High-cost";
    const out = r.visibleSessions();
    expect(out.length).toBe(1);
    expect(out[0].pid).toBe(2);
  });

  it("filter=Bookmarked returns only bookmarked sessions", () => {
    r.filter = "Bookmarked";
    // sessionKey("${pid}:${started_at || ''}") — started_at is "" in fixtures
    r.bookmarks = ["1:"];
    const out = r.visibleSessions();
    expect(out.length).toBe(1);
    expect(out[0].pid).toBe(1);
  });
});

describe("visibleSessions search-query substring", () => {
  let r;
  beforeEach(() => {
    r = newRoot();
    r.sessions = [
      sess({ pid: 1, cwd: "/repo/alpha", current_task_subject: "fix bug" }),
      sess({ pid: 2, cwd: "/repo/beta", iterm_tab_title: "Notes editor",
             last_activity_at: "2026-05-17T11:00:00Z" }),
      sess({ pid: 3, cwd: "/repo/gamma", tool_calls: { last_used: "Grep" },
             last_activity_at: "2026-05-17T09:00:00Z" }),
    ];
    r.filter = "All";
    r.bookmarks = [];
  });

  it("filters by cwd substring", () => {
    r._searchQueryDebounced = "beta";
    const out = r.visibleSessions();
    expect(out.length).toBe(1);
    expect(out[0].pid).toBe(2);
  });

  it("filters by current_task_subject", () => {
    r._searchQueryDebounced = "fix bug";
    const out = r.visibleSessions();
    expect(out.length).toBe(1);
    expect(out[0].pid).toBe(1);
  });

  it("filters by iterm tab title (case-insensitive)", () => {
    r._searchQueryDebounced = "notes";
    const out = r.visibleSessions();
    expect(out.length).toBe(1);
    expect(out[0].pid).toBe(2);
  });

  it("filters by last-used tool name", () => {
    r._searchQueryDebounced = "grep";
    const out = r.visibleSessions();
    expect(out.length).toBe(1);
    expect(out[0].pid).toBe(3);
  });

  it("empty query matches everything", () => {
    r._searchQueryDebounced = "";
    const out = r.visibleSessions();
    expect(out.length).toBe(3);
  });
});

describe("visibleSessions sorting", () => {
  let r;
  beforeEach(() => {
    r = newRoot();
    r.filter = "All";
    r._searchQueryDebounced = "";
  });

  it("bookmarked sessions sort before non-bookmarked", () => {
    r.sessions = [
      sess({ pid: 1, last_activity_at: "2026-05-17T08:00:00Z" }),
      sess({ pid: 2, last_activity_at: "2026-05-17T12:00:00Z" }),
      sess({ pid: 3, last_activity_at: "2026-05-17T10:00:00Z" }),
    ];
    // Bookmark the oldest pid (1). It should still come first.
    r.bookmarks = ["1:"];
    const out = r.visibleSessions();
    expect(out[0].pid).toBe(1);
    // The remaining two are sorted by last_activity desc.
    expect(out.slice(1).map((s) => s.pid)).toEqual([2, 3]);
  });

  it("non-bookmarked sessions sort by last_activity_at descending", () => {
    r.sessions = [
      sess({ pid: 1, last_activity_at: "2026-05-17T08:00:00Z" }),
      sess({ pid: 2, last_activity_at: "2026-05-17T12:00:00Z" }),
      sess({ pid: 3, last_activity_at: "2026-05-17T10:00:00Z" }),
    ];
    r.bookmarks = [];
    const out = r.visibleSessions();
    expect(out.map((s) => s.pid)).toEqual([2, 3, 1]);
  });
});

describe("sessionKey + isBookmarked", () => {
  it("sessionKey is `pid:started_at`", () => {
    const r = newRoot();
    expect(r.sessionKey({ pid: 7, started_at: "2026-05-17T00:00:00Z" }))
      .toBe("7:2026-05-17T00:00:00Z");
    expect(r.sessionKey({ pid: 7 })).toBe("7:");
  });

  it("isBookmarked checks for sessionKey membership", () => {
    const r = newRoot();
    r.bookmarks = ["1:", "2:foo"];
    expect(r.isBookmarked({ pid: 1, started_at: "" })).toBe(true);
    expect(r.isBookmarked({ pid: 2, started_at: "foo" })).toBe(true);
    expect(r.isBookmarked({ pid: 3, started_at: "" })).toBe(false);
  });
});
