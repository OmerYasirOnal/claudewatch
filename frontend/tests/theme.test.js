// Tests for the light/dark/auto theme toggle:
//   - setTheme(t) sets the `dark` class on <html> and persists.
//   - "auto" defers to window.matchMedia('(prefers-color-scheme: dark)').
//   - init() reads localStorage on startup and applies.
//   - Invalid persisted values fall through to "auto".
//
// We invoke setTheme directly rather than rendering the SVG button, so
// these tests stay focused on the state machine. Browser-level snapshot
// coverage is intentionally out of scope here.
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import "../app.js";

function newRoot() {
  return globalThis.__claudewatchAppRoot();
}

/**
 * Replace window.matchMedia with a controllable stub. Returns the
 * MediaQueryList so individual tests can flip `.matches` and fire
 * the "change" listener registered by _initTheme.
 */
function stubMatchMedia(initialMatches) {
  const listeners = new Set();
  const mql = {
    matches: !!initialMatches,
    media: "(prefers-color-scheme: dark)",
    addEventListener: (_evt, cb) => listeners.add(cb),
    removeEventListener: (_evt, cb) => listeners.delete(cb),
    // Legacy fallbacks; not used by app.js when addEventListener exists.
    addListener: (cb) => listeners.add(cb),
    removeListener: (cb) => listeners.delete(cb),
    dispatchEvent: () => true,
    _fire() { for (const cb of listeners) cb({ matches: this.matches }); },
  };
  // jsdom's window.matchMedia is undefined by default — define our stub.
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: vi.fn().mockReturnValue(mql),
  });
  return mql;
}

beforeEach(() => {
  // Fresh slate per test.
  document.documentElement.className = "";
  if (document.body) document.body.className = "";
  try { localStorage.clear(); } catch (e) { /* ignore */ }
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("setTheme — class toggle", () => {
  it("setTheme('dark') adds the 'dark' class to documentElement", () => {
    stubMatchMedia(false);
    const r = newRoot();
    r.setTheme("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(document.documentElement.classList.contains("light")).toBe(false);
  });

  it("setTheme('light') removes 'dark' and adds 'light'", () => {
    stubMatchMedia(true); // even if OS is dark
    const r = newRoot();
    r.setTheme("light");
    expect(document.documentElement.classList.contains("dark")).toBe(false);
    expect(document.documentElement.classList.contains("light")).toBe(true);
  });

  it("setTheme('auto') follows the mocked matchMedia state — dark when OS is dark", () => {
    const mql = stubMatchMedia(true);
    const r = newRoot();
    r._initTheme();   // wires up _themeMql
    r.setTheme("auto");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    // Flip the OS preference and fire the change event — app.js subscribed.
    mql.matches = false;
    mql._fire();
    expect(document.documentElement.classList.contains("dark")).toBe(false);
    expect(document.documentElement.classList.contains("light")).toBe(true);
  });

  it("setTheme('auto') stays light when OS prefers light", () => {
    stubMatchMedia(false);
    const r = newRoot();
    r._initTheme();
    r.setTheme("auto");
    expect(document.documentElement.classList.contains("dark")).toBe(false);
  });
});

describe("setTheme — persistence + sanitization", () => {
  it("persists the chosen theme to localStorage", () => {
    stubMatchMedia(false);
    const r = newRoot();
    r.setTheme("dark");
    expect(localStorage.getItem("claudewatch.theme")).toBe("dark");
    r.setTheme("auto");
    expect(localStorage.getItem("claudewatch.theme")).toBe("auto");
  });

  it("falls back to 'auto' on unknown values", () => {
    stubMatchMedia(false);
    const r = newRoot();
    r.setTheme("rainbow");
    expect(r.theme).toBe("auto");
    expect(localStorage.getItem("claudewatch.theme")).toBe("auto");
  });
});

describe("_loadLocalPrefs — startup hydration", () => {
  it("reads claudewatch.theme on startup and applies it", () => {
    stubMatchMedia(false);
    localStorage.setItem("claudewatch.theme", "dark");
    const r = newRoot();
    r._loadLocalPrefs();
    r._initTheme();
    expect(r.theme).toBe("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });

  it("migrates legacy claudewatch.appearance when no new key is set", () => {
    stubMatchMedia(false);
    localStorage.setItem("claudewatch.appearance", "light");
    const r = newRoot();
    r._loadLocalPrefs();
    r._initTheme();
    expect(r.theme).toBe("light");
    expect(document.documentElement.classList.contains("light")).toBe(true);
  });

  it("defaults to 'auto' when neither key is set", () => {
    stubMatchMedia(false);
    const r = newRoot();
    r._loadLocalPrefs();
    r._initTheme();
    expect(r.theme).toBe("auto");
  });
});

describe("cycleTheme", () => {
  it("cycles light → dark → auto → light", () => {
    stubMatchMedia(false);
    const r = newRoot();
    r.setTheme("light");
    r.cycleTheme();
    expect(r.theme).toBe("dark");
    r.cycleTheme();
    expect(r.theme).toBe("auto");
    r.cycleTheme();
    expect(r.theme).toBe("light");
  });
});

describe("themeTooltip", () => {
  it("returns a descriptive tooltip for each state", () => {
    const r = newRoot();
    r.theme = "light"; expect(r.themeTooltip()).toMatch(/Light/);
    r.theme = "dark";  expect(r.themeTooltip()).toMatch(/Dark/);
    r.theme = "auto";  expect(r.themeTooltip()).toMatch(/Auto|system/i);
  });
});
