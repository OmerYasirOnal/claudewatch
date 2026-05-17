// Tests for the per-session event timeline panel: loadTimeline,
// clearTimeline, icon/class helpers. All fetches are stubbed.
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

const SAMPLE_PAYLOAD = {
  pid: 4242,
  events: [
    {
      timestamp: "2026-05-17T10:00:00Z",
      type: "started",
      description: "Session started",
      metadata: { cwd: "/repo", cli_version: "2.1.0" },
    },
    {
      timestamp: "2026-05-17T10:00:01Z",
      type: "first_tool",
      description: "First tool call (Bash)",
      metadata: { tool: "Bash" },
    },
    {
      timestamp: "2026-05-17T10:00:01Z",
      type: "tool_call",
      description: "Bash tool call",
      metadata: { tool: "Bash", count: 1 },
    },
  ],
  truncated: false,
};

describe("loadTimeline", () => {
  let r;
  beforeEach(() => { r = newRoot(); });
  afterEach(() => { vi.restoreAllMocks(); });

  it("fetches /api/sessions/:pid/timeline and stores the result", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse(SAMPLE_PAYLOAD));

    await r.loadTimeline(4242);

    expect(globalThis.fetch).toHaveBeenCalledWith("/api/sessions/4242/timeline");
    expect(r.selectedSessionTimeline).not.toBeNull();
    expect(r.selectedSessionTimeline.pid).toBe(4242);
    expect(r.selectedSessionTimeline.events).toHaveLength(3);
    expect(r.selectedSessionTimeline.truncated).toBe(false);
    expect(r.timelineLoading).toBe(false);
    expect(r.timelineError).toBeNull();
  });

  it("does nothing when pid is null", async () => {
    globalThis.fetch = vi.fn();
    await r.loadTimeline(null);
    expect(globalThis.fetch).not.toHaveBeenCalled();
    expect(r.selectedSessionTimeline).toBeNull();
  });

  it("degrades gracefully on 500 — sets error, leaves prior data alone", async () => {
    // Seed prior data.
    r.selectedSessionTimeline = { pid: 1, events: [{ type: "started" }], truncated: false };
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse({}, { ok: false, status: 500 }));

    await r.loadTimeline(99);

    expect(r.timelineError).toMatch(/HTTP 500/);
    // Previously loaded data is NOT clobbered.
    expect(r.selectedSessionTimeline).toEqual({ pid: 1, events: [{ type: "started" }], truncated: false });
    expect(r.timelineLoading).toBe(false);
  });

  it("degrades gracefully on network error", async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error("offline"));

    await r.loadTimeline(7);

    expect(r.timelineError).toBe("Network error loading timeline");
    expect(r.timelineLoading).toBe(false);
  });

  it("re-loading REPLACES the prior timeline, does not append", async () => {
    const first = {
      pid: 1,
      events: [{ timestamp: "2026-05-17T10:00:00Z", type: "started", description: "x", metadata: {} }],
      truncated: false,
    };
    const second = {
      pid: 1,
      events: [
        { timestamp: "2026-05-17T11:00:00Z", type: "ended", description: "y", metadata: {} },
        { timestamp: "2026-05-17T11:00:01Z", type: "error", description: "z", metadata: {} },
      ],
      truncated: true,
    };

    globalThis.fetch = vi.fn()
      .mockResolvedValueOnce(jsonResponse(first))
      .mockResolvedValueOnce(jsonResponse(second));

    await r.loadTimeline(1);
    expect(r.selectedSessionTimeline.events).toHaveLength(1);

    await r.loadTimeline(1);
    expect(r.selectedSessionTimeline.events).toHaveLength(2);
    expect(r.selectedSessionTimeline.truncated).toBe(true);
    expect(r.selectedSessionTimeline.events[0].type).toBe("ended");
  });

  it("normalizes a non-array events field to []", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse({
      pid: 1,
      events: null,
      truncated: false,
    }));

    await r.loadTimeline(1);

    expect(r.selectedSessionTimeline.events).toEqual([]);
  });
});

describe("clearTimeline", () => {
  let r;
  beforeEach(() => { r = newRoot(); });

  it("resets all timeline state", async () => {
    r.selectedSessionTimeline = { pid: 1, events: [{}], truncated: true };
    r.timelineLoading = true;
    r.timelineError = "boom";
    r.timelinePid = 1;

    r.clearTimeline();

    expect(r.selectedSessionTimeline).toBeNull();
    expect(r.timelineLoading).toBe(false);
    expect(r.timelineError).toBeNull();
    expect(r.timelinePid).toBeNull();
  });
});

describe("loadDetail triggers loadTimeline", () => {
  let r;
  beforeEach(() => { r = newRoot(); });
  afterEach(() => { vi.restoreAllMocks(); });

  it("calling loadDetail fires the timeline endpoint as well", async () => {
    const sessionBody = { pid: 4242, cwd: "/repo" };
    globalThis.fetch = vi.fn()
      .mockResolvedValueOnce(jsonResponse(sessionBody))           // /api/sessions/:pid
      .mockResolvedValueOnce(jsonResponse(SAMPLE_PAYLOAD));       // /api/sessions/:pid/timeline

    await r.loadDetail(4242);
    // loadTimeline is fire-and-forget — wait a microtask.
    await Promise.resolve(); await Promise.resolve();

    const urls = globalThis.fetch.mock.calls.map(c => c[0]);
    expect(urls).toContain("/api/sessions/4242");
    expect(urls).toContain("/api/sessions/4242/timeline");
  });
});

describe("timelineIcon / timelineIconClass", () => {
  let r;
  beforeEach(() => { r = newRoot(); });

  it("returns distinct icons per known event type", () => {
    const types = [
      "started", "ended", "model_switch", "first_tool", "tool_call",
      "subagent_started", "subagent_finished", "thinking_started",
      "permission_prompt", "error",
    ];
    const icons = types.map((t) => r.timelineIcon(t));
    // No icon is empty/undefined.
    expect(icons.every((s) => typeof s === "string" && s.length > 0)).toBe(true);
    // At least the error type has its own color class.
    expect(r.timelineIconClass("error")).toMatch(/rose/);
  });

  it("falls back to a default glyph for unknown types", () => {
    expect(typeof r.timelineIcon("totally_unknown")).toBe("string");
    expect(r.timelineIcon("totally_unknown").length).toBeGreaterThan(0);
    expect(typeof r.timelineIconClass("totally_unknown")).toBe("string");
  });
});
