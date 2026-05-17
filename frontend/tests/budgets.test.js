// Tests for the cost-budgets frontend slice:
//  * configDraft budgets normalization (Settings tab inputs)
//  * loadBudgets() — fetch + payload parsing + error tolerance
//  * showBudgetsCard() — plan + cardVisibility gate
//  * budgetBarColor() — color thresholds (green / yellow / red)
//  * budgetLabel() — pretty window labels
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
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

describe("_normalizeConfigDraft fills budgets defaults", () => {
  let r;
  beforeEach(() => { r = newRoot(); });

  it("populates a missing budgets section with safe defaults", () => {
    r.config = { plan: "api" };
    r._syncConfigDraft();
    expect(r.configDraft.budgets).toBeDefined();
    expect(r.configDraft.budgets.enabled).toBe(false);
    expect(r.configDraft.budgets.daily_usd).toBe(5.0);
    expect(r.configDraft.budgets.weekly_usd).toBe(30.0);
    expect(r.configDraft.budgets.monthly_usd).toBe(100.0);
    expect(r.configDraft.budgets.warn_at_percent).toBe(80);
  });

  it("preserves user-supplied budgets values", () => {
    r.config = {
      plan: "api",
      budgets: { enabled: true, daily_usd: 2.5, weekly_usd: 15, monthly_usd: 60, warn_at_percent: 90 },
    };
    r._syncConfigDraft();
    expect(r.configDraft.budgets.enabled).toBe(true);
    expect(r.configDraft.budgets.daily_usd).toBe(2.5);
    expect(r.configDraft.budgets.weekly_usd).toBe(15);
    expect(r.configDraft.budgets.monthly_usd).toBe(60);
    expect(r.configDraft.budgets.warn_at_percent).toBe(90);
  });

  it("editing budgets in the draft flips configDirty=true via markConfigDirty", () => {
    r.config = { plan: "api", budgets: { enabled: false, daily_usd: 5, weekly_usd: 30, monthly_usd: 100, warn_at_percent: 80 } };
    r._syncConfigDraft();
    expect(r.configDirty).toBe(false);
    r.configDraft.budgets.enabled = true;
    r.markConfigDirty();
    expect(r.configDirty).toBe(true);
  });
});

describe("loadBudgets", () => {
  let r;
  beforeEach(() => { r = newRoot(); r.budgetsData = null; });
  afterEach(() => { vi.restoreAllMocks(); });

  it("parses the /api/budgets response into budgetsData", async () => {
    const payload = {
      enabled: true,
      warn_at_percent: 80,
      windows: [
        { window: "daily", hours: 24, budget_usd: 5, spent_usd: 2.4, percent: 48 },
        { window: "weekly", hours: 168, budget_usd: 30, spent_usd: 9.0, percent: 30 },
        { window: "monthly", hours: 720, budget_usd: 100, spent_usd: 19.0, percent: 19 },
      ],
    };
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse(payload));
    await r.loadBudgets();
    expect(globalThis.fetch).toHaveBeenCalledWith("/api/budgets");
    expect(r.budgetsData).toEqual(payload);
  });

  it("leaves budgetsData unchanged when fetch returns !ok", async () => {
    r.budgetsData = { enabled: false, warn_at_percent: 80, windows: [] };
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse({}, { ok: false, status: 500 }));
    await r.loadBudgets();
    expect(r.budgetsData).toEqual({ enabled: false, warn_at_percent: 80, windows: [] });
  });

  it("swallows network errors silently", async () => {
    r.budgetsData = null;
    globalThis.fetch = vi.fn().mockRejectedValue(new Error("offline"));
    await r.loadBudgets();
    expect(r.budgetsData).toBeNull();
  });
});

describe("showBudgetsCard plan + cardVisibility gating", () => {
  let r;
  beforeEach(() => { r = newRoot(); });

  it("true when plan==='api' and cost card is visible", () => {
    r.config = { plan: "api" };
    r.cardVisibility = { ...r.cardVisibility, cost: true };
    expect(r.showBudgetsCard()).toBe(true);
  });

  it("false when plan is not 'api' (Max/Pro users)", () => {
    r.config = { plan: "max" };
    r.cardVisibility = { ...r.cardVisibility, cost: true };
    expect(r.showBudgetsCard()).toBe(false);
  });

  it("false when cardVisibility.cost is explicitly false", () => {
    r.config = { plan: "api" };
    r.cardVisibility = { ...r.cardVisibility, cost: false };
    expect(r.showBudgetsCard()).toBe(false);
  });
});

describe("budgetBarColor thresholds", () => {
  let r;
  beforeEach(() => { r = newRoot(); });

  it("green when percent < 50", () => {
    expect(r.budgetBarColor(0)).toBe("bg-emerald-500");
    expect(r.budgetBarColor(49.999)).toBe("bg-emerald-500");
  });

  it("yellow when percent is in [50, 80]", () => {
    expect(r.budgetBarColor(50)).toBe("bg-amber-500");
    expect(r.budgetBarColor(75)).toBe("bg-amber-500");
    expect(r.budgetBarColor(80)).toBe("bg-amber-500");
  });

  it("red when percent > 80", () => {
    expect(r.budgetBarColor(80.0001)).toBe("bg-rose-500");
    expect(r.budgetBarColor(100)).toBe("bg-rose-500");
    expect(r.budgetBarColor(150)).toBe("bg-rose-500");
  });

  it("coerces non-numeric inputs to 0 (green)", () => {
    expect(r.budgetBarColor(null)).toBe("bg-emerald-500");
    expect(r.budgetBarColor(undefined)).toBe("bg-emerald-500");
    expect(r.budgetBarColor("not a number")).toBe("bg-emerald-500");
  });
});

describe("budgetLabel renders friendly window names", () => {
  let r;
  beforeEach(() => { r = newRoot(); });

  it("maps the three canonical windows", () => {
    expect(r.budgetLabel("daily")).toBe("Daily (24h)");
    expect(r.budgetLabel("weekly")).toBe("Weekly (7d)");
    expect(r.budgetLabel("monthly")).toBe("Monthly (30d)");
  });

  it("falls back to the raw window name for unknown values", () => {
    expect(r.budgetLabel("quarterly")).toBe("quarterly");
  });
});

describe("saveConfigDraft persists budgets", () => {
  let r;
  beforeEach(() => { r = newRoot(); });
  afterEach(() => { vi.restoreAllMocks(); });

  it("POSTs the budgets section as part of /api/config", async () => {
    r.config = { plan: "api", notifications: {}, remote_control: { enabled: false }, editor: { enabled: false, command: "code" }, budgets: { enabled: false, daily_usd: 5, weekly_usd: 30, monthly_usd: 100, warn_at_percent: 80 }, pricing: {} };
    r._syncConfigDraft();
    r.configDraft.budgets.enabled = true;
    r.configDraft.budgets.daily_usd = 3.0;
    r.markConfigDirty();

    const server = JSON.parse(JSON.stringify(r.configDraft));
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse(server));

    await r.saveConfigDraft();

    expect(globalThis.fetch).toHaveBeenCalledTimes(1);
    const [, init] = globalThis.fetch.mock.calls[0];
    const body = JSON.parse(init.body);
    expect(body.budgets.enabled).toBe(true);
    expect(body.budgets.daily_usd).toBe(3.0);
    // Post-save: dirty cleared, config now matches the server echo.
    expect(r.configDirty).toBe(false);
    expect(r.config.budgets.enabled).toBe(true);
  });
});
