// Tests for the Observability sparklines on the Status tab — rolling buffer,
// delta-vs-gauge handling, sparklineLast() formatting, and defensive canvas
// rendering. fetch is stubbed in every test; nothing touches the network.
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

// Minimal counter payload — only the fields the sparkline buffer cares about.
function payload({ ticks = 0, broadcasts = 0, iterm = 0, tickMsMax = 0 } = {}) {
  return {
    scheduler_ticks_total: ticks,
    scheduler_tick_duration_ms_sum: 0,
    scheduler_tick_duration_ms_max: tickMsMax,
    scheduler_tick_duration_ms_avg: 0,
    iterm_refresh_total: iterm,
    iterm_refresh_duration_ms_sum: 0,
    iterm_refresh_duration_ms_avg: 0,
    iterm_refresh_failures_total: 0,
    broadcasts_total: broadcasts,
    sse_subscribers: 0,
    detector_failures_total: 0,
    process_scan_last_count: 0,
    started_at: "2026-05-17T12:00:00Z",
    uptime_seconds: 0,
  };
}

describe("observability — metricsHistory buffer", () => {
  let r;
  beforeEach(() => { r = newRoot(); });
  afterEach(() => { vi.restoreAllMocks(); });

  it("starts empty for all four series", () => {
    expect(r.metricsHistory).toEqual({
      ticks: [],
      broadcasts: [],
      tickMs: [],
      iterm: [],
    });
  });

  it("computes the per-tick delta between two loadMetrics() calls", async () => {
    globalThis.fetch = vi.fn()
      .mockResolvedValueOnce(jsonResponse(payload({ ticks: 100, broadcasts: 30, iterm: 10, tickMsMax: 4 })))
      .mockResolvedValueOnce(jsonResponse(payload({ ticks: 105, broadcasts: 35, iterm: 12, tickMsMax: 7 })));

    await r.loadMetrics();
    await r.loadMetrics();

    // First sample establishes a baseline → delta = 0. Second sample shows
    // the increment for the counters; the gauge stores the raw value.
    expect(r.metricsHistory.ticks.length).toBe(2);
    expect(r.metricsHistory.ticks[0].v).toBe(0);
    expect(r.metricsHistory.ticks[1].v).toBe(5);

    expect(r.metricsHistory.broadcasts[1].v).toBe(5);
    expect(r.metricsHistory.iterm[1].v).toBe(2);

    // tickMs is a gauge — raw value, not delta.
    expect(r.metricsHistory.tickMs[0].v).toBe(4);
    expect(r.metricsHistory.tickMs[1].v).toBe(7);
  });

  it("caps each buffer at 60 entries and drops the oldest", async () => {
    // Push 65 monotonically-increasing samples.
    const fetches = [];
    for (let i = 1; i <= 65; i++) {
      fetches.push(jsonResponse(payload({ ticks: i, broadcasts: i * 2, iterm: i, tickMsMax: i })));
    }
    let call = 0;
    globalThis.fetch = vi.fn().mockImplementation(() => Promise.resolve(fetches[call++]));

    for (let i = 0; i < 65; i++) {
      await r.loadMetrics();
    }

    expect(r.metricsHistory.ticks.length).toBe(60);
    expect(r.metricsHistory.broadcasts.length).toBe(60);
    expect(r.metricsHistory.tickMs.length).toBe(60);
    expect(r.metricsHistory.iterm.length).toBe(60);
    // The oldest 5 were dropped, so the first remaining tickMs gauge value
    // should reflect sample #6 (1-indexed).
    expect(r.metricsHistory.tickMs[0].v).toBe(6);
    expect(r.metricsHistory.tickMs[59].v).toBe(65);
  });

  it("clamps negative counter deltas to 0 (defensive — counter shouldn't decrease)", async () => {
    // Daemon-restart scenario: counter goes 1000 → 5 on the next poll.
    globalThis.fetch = vi.fn()
      .mockResolvedValueOnce(jsonResponse(payload({ ticks: 1000, broadcasts: 500 })))
      .mockResolvedValueOnce(jsonResponse(payload({ ticks: 5, broadcasts: 2 })));

    await r.loadMetrics();
    await r.loadMetrics();

    expect(r.metricsHistory.ticks[1].v).toBe(0);
    expect(r.metricsHistory.broadcasts[1].v).toBe(0);
  });
});

describe("observability — sparklineLast()", () => {
  let r;
  beforeEach(() => { r = newRoot(); });
  afterEach(() => { vi.restoreAllMocks(); });

  it("returns '—' when the requested buffer is empty", () => {
    expect(r.sparklineLast("ticks")).toBe("—");
    expect(r.sparklineLast("broadcasts")).toBe("—");
    expect(r.sparklineLast("tickMs")).toBe("—");
    expect(r.sparklineLast("iterm")).toBe("—");
  });

  it("formats counter deltas as per-minute and the gauge in ms", async () => {
    // 5s poll cadence → delta * 12 = per-minute. Use 10 → "120 t/min".
    globalThis.fetch = vi.fn()
      .mockResolvedValueOnce(jsonResponse(payload({ ticks: 0, broadcasts: 0, iterm: 0, tickMsMax: 0 })))
      .mockResolvedValueOnce(jsonResponse(payload({ ticks: 10, broadcasts: 4, iterm: 1, tickMsMax: 8.42 })));

    await r.loadMetrics();
    await r.loadMetrics();

    expect(r.sparklineLast("ticks")).toBe("120 t/min");
    expect(r.sparklineLast("broadcasts")).toBe("48 b/min");
    expect(r.sparklineLast("iterm")).toBe("12 r/min");
    expect(r.sparklineLast("tickMs")).toBe("8.4 ms");
  });
});

describe("observability — sparkline rendering", () => {
  let r;
  beforeEach(() => { r = newRoot(); });
  afterEach(() => { vi.restoreAllMocks(); });

  it("does not throw when the canvas refs are missing (jsdom has no canvas)", async () => {
    // No $refs assigned → _renderObservabilitySparklines must gracefully no-op.
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse(payload({ ticks: 1, broadcasts: 1, iterm: 1, tickMsMax: 1 })));

    await expect(r.loadMetrics()).resolves.toBeUndefined();
    // Direct invocation should also be safe.
    expect(() => r._renderObservabilitySparklines()).not.toThrow();
    expect(() => r._renderSparkline(null, [{ t: 1, v: 1 }], {})).not.toThrow();
    expect(() => r._renderSparkline(undefined, [], {})).not.toThrow();
  });
});
