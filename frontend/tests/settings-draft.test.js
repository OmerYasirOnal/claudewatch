// Tests for the staged-save Settings flow (configDraft / configDirty /
// saveConfigDraft / revertConfig).
//
// app.js uses `configDraft` + `configDirty` (not isDirty) and the save
// function is `saveConfigDraft()` (the bare `saveConfig()` is the
// bypass path used by inline edits like pricing).
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

describe("loadConfig + _syncConfigDraft", () => {
  let r;
  beforeEach(() => { r = newRoot(); });
  afterEach(() => { vi.restoreAllMocks(); });

  it("loadConfig populates config and deep-clones it into configDraft", async () => {
    const payload = {
      plan: "max5x",
      notifications: { enabled: true, on_session_end: false, on_high_cost: true, cost_threshold_usd: 2.5 },
      remote_control: { enabled: true },
      editor: { enabled: true, command: "vim" },
      pricing: { "claude-opus-4-7": { input: 15, output: 75 } },
    };
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse(payload));

    await r.loadConfig();

    expect(r.config.plan).toBe("max5x");
    expect(r.configDraft).toEqual(r.config);
    // Deep clone: mutating draft does NOT touch config.
    r.configDraft.notifications.enabled = false;
    expect(r.config.notifications.enabled).toBe(true);
    expect(r.configDirty).toBe(false);
    // chatRemoteEnabled mirrors remote_control.enabled.
    expect(r.chatRemoteEnabled).toBe(true);
  });

  it("_syncConfigDraft normalizes missing nested objects", () => {
    r.config = { plan: "api" };
    r._syncConfigDraft();
    expect(r.configDraft.notifications).toEqual({});
    expect(r.configDraft.remote_control).toEqual({ enabled: false });
    expect(r.configDraft.editor).toEqual({ enabled: false, command: "code" });
    expect(r.configDirty).toBe(false);
  });

  it("loadConfig surfaces an error banner on a non-200 response", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse({}, { ok: false, status: 500 }));

    await r.loadConfig();

    expect(r.errorBanner).not.toBeNull();
    expect(r.errorBanner.text).toMatch(/\/api\/config/);
    expect(r.errorBanner.text).toMatch(/HTTP 500/);
  });
});

describe("markConfigDirty", () => {
  let r;
  beforeEach(() => { r = newRoot(); });

  it("flips configDirty=true when configDraft diverges from config", () => {
    r.config = { plan: "api", notifications: { enabled: false }, remote_control: { enabled: false }, editor: { enabled: false, command: "code" } };
    r._syncConfigDraft();
    expect(r.configDirty).toBe(false);

    r.configDraft.notifications.enabled = true;
    r.markConfigDirty();
    expect(r.configDirty).toBe(true);
  });

  it("stays false when configDraft and config are identical", () => {
    r.config = { plan: "api", notifications: {}, remote_control: { enabled: false }, editor: { enabled: false, command: "code" } };
    r._syncConfigDraft();
    r.markConfigDirty();
    expect(r.configDirty).toBe(false);
  });

  it("resets configSavingState='saved' back to 'idle' on further edits", () => {
    r.config = { plan: "api", notifications: {}, remote_control: { enabled: false }, editor: { enabled: false, command: "code" } };
    r._syncConfigDraft();
    r.configSavingState = "saved";
    r.configDraft.plan = "max5x";
    r.markConfigDirty();
    expect(r.configSavingState).toBe("idle");
  });

  it("clears a prior error message when the user edits again", () => {
    r.config = { plan: "api", notifications: {}, remote_control: { enabled: false }, editor: { enabled: false, command: "code" } };
    r._syncConfigDraft();
    r.configSavingState = "error";
    r.configSaveError = "previous failure";
    r.configDraft.plan = "max20x";
    r.markConfigDirty();
    expect(r.configSavingState).toBe("idle");
    expect(r.configSaveError).toBe("");
  });
});

describe("revertConfig", () => {
  let r;
  beforeEach(() => { r = newRoot(); });

  it("resets configDraft back to config and clears dirty / error state", () => {
    r.config = { plan: "api", notifications: { enabled: false }, remote_control: { enabled: false }, editor: { enabled: false, command: "code" } };
    r._syncConfigDraft();
    r.configDraft.notifications.enabled = true;
    r.markConfigDirty();
    r.configSavingState = "error";
    r.configSaveError = "boom";

    r.revertConfig();

    expect(r.configDraft.notifications.enabled).toBe(false);
    expect(r.configDirty).toBe(false);
    expect(r.configSavingState).toBe("idle");
    expect(r.configSaveError).toBe("");
  });

  it("revert produces a deep clone — mutating draft after revert doesn't touch config", () => {
    r.config = { plan: "api", notifications: { enabled: true }, remote_control: { enabled: false }, editor: { enabled: false, command: "code" } };
    r._syncConfigDraft();
    r.revertConfig();
    r.configDraft.notifications.enabled = false;
    expect(r.config.notifications.enabled).toBe(true);
  });
});

describe("saveConfigDraft", () => {
  let r;
  beforeEach(() => { r = newRoot(); });
  afterEach(() => { vi.restoreAllMocks(); });

  it("no-ops (no fetch) when configDirty is false", async () => {
    globalThis.fetch = vi.fn();
    r.configDirty = false;
    await r.saveConfigDraft();
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it("POSTs configDraft to /api/config and resets configDirty on success", async () => {
    r.config = { plan: "api", notifications: { enabled: false }, remote_control: { enabled: false }, editor: { enabled: false, command: "code" }, pricing: {} };
    r._syncConfigDraft();
    r.configDraft.notifications.enabled = true;
    r.markConfigDirty();
    expect(r.configDirty).toBe(true);

    const updatedFromServer = JSON.parse(JSON.stringify(r.configDraft));
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse(updatedFromServer));

    await r.saveConfigDraft();

    expect(globalThis.fetch).toHaveBeenCalledTimes(1);
    const [url, init] = globalThis.fetch.mock.calls[0];
    expect(url).toBe("/api/config");
    expect(init.method).toBe("POST");
    expect(init.headers).toEqual({ "Content-Type": "application/json" });
    // Body matches what was in configDraft at the time of save.
    expect(JSON.parse(init.body).notifications.enabled).toBe(true);

    // Post-save: config now equals what the server returned, draft re-synced,
    // dirty flag cleared, state is "saved".
    expect(r.config.notifications.enabled).toBe(true);
    expect(r.configDirty).toBe(false);
    expect(r.configSavingState).toBe("saved");
    expect(r.configSaveError).toBe("");
  });

  it("sets configSavingState='error' and surfaces detail on non-200", async () => {
    r.config = { plan: "api", notifications: {}, remote_control: { enabled: false }, editor: { enabled: false, command: "code" } };
    r._syncConfigDraft();
    r.configDraft.plan = "max20x";
    r.markConfigDirty();

    globalThis.fetch = vi.fn().mockResolvedValue(
      jsonResponse({ detail: "invalid plan" }, { ok: false, status: 422 }),
    );

    await r.saveConfigDraft();

    expect(r.configSavingState).toBe("error");
    expect(r.configSaveError).toBe("invalid plan");
    // Dirty stays true because the save did not succeed.
    expect(r.configDirty).toBe(true);
  });

  it("falls back to 'HTTP <status>' when error body has no detail", async () => {
    r.config = { plan: "api", notifications: {}, remote_control: { enabled: false }, editor: { enabled: false, command: "code" } };
    r._syncConfigDraft();
    r.configDraft.plan = "max20x";
    r.markConfigDirty();

    // json() throws → caught → err = {}; thrown Error("HTTP 500")
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => { throw new Error("not json"); },
    });

    await r.saveConfigDraft();

    expect(r.configSavingState).toBe("error");
    expect(r.configSaveError).toMatch(/HTTP 500/);
  });

  it("captures network errors into configSaveError", async () => {
    r.config = { plan: "api", notifications: {}, remote_control: { enabled: false }, editor: { enabled: false, command: "code" } };
    r._syncConfigDraft();
    r.configDraft.plan = "max5x";
    r.markConfigDirty();

    globalThis.fetch = vi.fn().mockRejectedValue(new Error("offline"));

    await r.saveConfigDraft();

    expect(r.configSavingState).toBe("error");
    expect(r.configSaveError).toBe("offline");
    expect(r.configDirty).toBe(true);
  });
});

describe("saveConfig (bypass / inline-edit path)", () => {
  let r;
  beforeEach(() => { r = newRoot(); });
  afterEach(() => { vi.restoreAllMocks(); });

  it("POSTs the partial updates and updates this.config", async () => {
    r.config = { plan: "api", notifications: {}, remote_control: { enabled: false }, editor: { enabled: false, command: "code" }, pricing: {} };
    r._syncConfigDraft(); // dirty=false

    const server = { ...r.config, plan: "max5x" };
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse(server));

    await r.saveConfig({ plan: "max5x" });

    const [url, init] = globalThis.fetch.mock.calls[0];
    expect(url).toBe("/api/config");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ plan: "max5x" });
    expect(r.config.plan).toBe("max5x");
    // Since draft wasn't dirty, it is re-synced.
    expect(r.configDraft.plan).toBe("max5x");
    expect(r.configDirty).toBe(false);
  });

  it("does NOT clobber a dirty draft when called from the bypass path", async () => {
    r.config = { plan: "api", notifications: { enabled: false }, remote_control: { enabled: false }, editor: { enabled: false, command: "code" }, pricing: {} };
    r._syncConfigDraft();
    // User has staged a draft edit that hasn't been saved yet.
    r.configDraft.notifications.enabled = true;
    r.markConfigDirty();
    expect(r.configDirty).toBe(true);

    // Server response represents a different concurrent change (e.g. pricing).
    const server = { ...r.config, pricing: { "claude-opus": { input: 1, output: 2 } } };
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse(server));

    await r.saveConfig({ pricing: server.pricing });

    // config is updated…
    expect(r.config.pricing["claude-opus"].input).toBe(1);
    // …but the draft's staged notification edit is preserved.
    expect(r.configDraft.notifications.enabled).toBe(true);
    expect(r.configDirty).toBe(true);
  });
});
