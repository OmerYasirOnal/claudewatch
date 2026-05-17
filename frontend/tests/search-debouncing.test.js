// Tests for the search input debounce: onSearchInput() schedules a 150ms
// timer that copies the trimmed/lowercased query into _searchQueryDebounced.
// Rapid typing cancels in-flight timers so we only commit the latest value.
//
// All timer behavior is exercised with fake timers; visibleSessions() is
// also probed to confirm the debounced field is what actually drives the
// filter (empty == match everything).
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
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
    cwd: "/repo/alpha",
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

describe("onSearchInput debounce", () => {
  let r;
  beforeEach(() => {
    r = newRoot();
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("immediately stores the raw value in searchQuery, but defers _searchQueryDebounced", () => {
    r.onSearchInput("alpha");

    // Raw field updates synchronously.
    expect(r.searchQuery).toBe("alpha");
    // Debounced mirror does NOT update until the timer fires.
    expect(r._searchQueryDebounced).toBe("");
    expect(r._searchDebounce).not.toBeNull();

    vi.advanceTimersByTime(150);
    expect(r._searchQueryDebounced).toBe("alpha");
  });

  it("trims and lowercases the debounced value", () => {
    r.onSearchInput("  Hello WORLD  ");
    vi.advanceTimersByTime(150);
    expect(r._searchQueryDebounced).toBe("hello world");
  });

  it("rapid typing cancels the previous timer so only the latest commits", () => {
    r.onSearchInput("a");
    vi.advanceTimersByTime(50);
    r.onSearchInput("ab");
    vi.advanceTimersByTime(50);
    r.onSearchInput("abc");
    // Total elapsed = 100 ms; final-debounce timer should NOT have fired yet.
    expect(r._searchQueryDebounced).toBe("");

    vi.advanceTimersByTime(150);
    // Only the latest value should land.
    expect(r._searchQueryDebounced).toBe("abc");
  });

  it("does not fire before the 150ms window elapses", () => {
    r.onSearchInput("partial");
    vi.advanceTimersByTime(149);
    expect(r._searchQueryDebounced).toBe("");

    vi.advanceTimersByTime(1);
    expect(r._searchQueryDebounced).toBe("partial");
  });

  it("clearing the input ('') schedules a debounce that resets _searchQueryDebounced to ''", () => {
    r._searchQueryDebounced = "stale";
    r.onSearchInput("");
    // Timer is scheduled; advance.
    vi.advanceTimersByTime(150);
    expect(r._searchQueryDebounced).toBe("");
  });
});

describe("debounced query → visibleSessions integration", () => {
  let r;
  beforeEach(() => {
    r = newRoot();
    r.filter = "All";
    r.bookmarks = [];
    r.sessions = [
      sess({ pid: 1, cwd: "/repo/alpha" }),
      sess({ pid: 2, cwd: "/repo/beta", last_activity_at: "2026-05-17T11:00:00Z" }),
      sess({ pid: 3, cwd: "/repo/gamma", last_activity_at: "2026-05-17T09:00:00Z" }),
    ];
  });

  it("empty _searchQueryDebounced matches everything", () => {
    r._searchQueryDebounced = "";
    expect(r.visibleSessions().length).toBe(3);
  });

  it("only the debounced field (not searchQuery) drives the filter", () => {
    // User has typed but the debounce hasn't fired yet — the visible list
    // should still show everything.
    r.searchQuery = "beta";
    r._searchQueryDebounced = "";
    expect(r.visibleSessions().length).toBe(3);

    // Once the debounced field lands, the filter applies.
    r._searchQueryDebounced = "beta";
    expect(r.visibleSessions().map((s) => s.pid)).toEqual([2]);
  });
});
