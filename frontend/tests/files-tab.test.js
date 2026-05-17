// Tests for the Files tab loaders: loadFileChanges, loadDiff (the actual
// name used by selectFileChange/openFileDiff in app.js), closeFileDiff,
// visibleFileChanges, and the Files-tab polling timer driven by
// _onViewChange. All fetches are stubbed; nothing touches the running
// claude session or backend.
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

describe("loadFileChanges", () => {
  let r;
  beforeEach(() => { r = newRoot(); });
  afterEach(() => { vi.restoreAllMocks(); });

  it("fetches /api/file-changes with the configured minutes and stores results", async () => {
    const payload = [
      { path: "a.py", cwd: "/repo", kind: "modified", ts: "2026-05-17T10:00:00Z" },
      { path: "b.py", cwd: "/repo", kind: "created", ts: "2026-05-17T11:00:00Z" },
    ];
    r.filesMinutes = 30;
    r.fileChangesError = "stale";
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse(payload));

    await r.loadFileChanges();

    expect(globalThis.fetch).toHaveBeenCalledWith("/api/file-changes?minutes=30");
    expect(r.fileChanges).toEqual(payload);
    expect(r.fileChangesError).toBeNull();
    expect(r.fileChangesUnavailable).toBe(false);
    expect(r.filesLastRefresh).not.toBeNull();
  });

  it("normalizes {changes: [...]} body to an array", async () => {
    const payload = { changes: [{ path: "x.py" }] };
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse(payload));

    await r.loadFileChanges();

    expect(r.fileChanges).toEqual([{ path: "x.py" }]);
  });

  it("defaults minutes to 10 when filesMinutes is falsy", async () => {
    r.filesMinutes = 0;
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse([]));

    await r.loadFileChanges();

    expect(globalThis.fetch).toHaveBeenCalledWith("/api/file-changes?minutes=10");
  });

  it("sets fileChangesUnavailable=true and clears the list on 404", async () => {
    r.fileChanges = [{ path: "stale" }];
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse({}, { ok: false, status: 404 }));

    await r.loadFileChanges();

    expect(r.fileChangesUnavailable).toBe(true);
    expect(r.fileChanges).toEqual([]);
    expect(r.filesLastRefresh).not.toBeNull();
  });

  it("sets fileChangesError on non-200 non-404 HTTP responses", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse({}, { ok: false, status: 500 }));

    await r.loadFileChanges();

    expect(r.fileChangesError).toMatch(/HTTP 500/);
    expect(r.fileChangesError).toMatch(/\/api\/file-changes/);
  });

  it("sets fileChangesError on network error", async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error("offline"));

    await r.loadFileChanges();

    expect(r.fileChangesError).toBe("Network error loading file changes");
  });
});

describe("selectFileChange / loadDiff / closeFileDiff", () => {
  let r;
  beforeEach(() => { r = newRoot(); });
  afterEach(() => { vi.restoreAllMocks(); });

  it("selectFileChange populates filesSelected and triggers loadDiff to /api/files/diff", async () => {
    const diffPayload = { diff: "--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new\n" };
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse(diffPayload));

    r.selectFileChange({ cwd: "/repo", path: "foo.py", project: "repo", abs_path: "/repo/foo.py" });
    // selectFileChange synchronously sets filesSelected and calls loadDiff()
    expect(r.filesSelected).toEqual({ cwd: "/repo", path: "foo.py", project: "repo", abs_path: "/repo/foo.py" });

    // Let the in-flight loadDiff resolve.
    await Promise.resolve(); await Promise.resolve(); await Promise.resolve();

    const url = globalThis.fetch.mock.calls[0][0];
    expect(url).toMatch(/^\/api\/files\/diff\?/);
    expect(url).toContain("cwd=%2Frepo");
    expect(url).toContain("path=foo.py");
    expect(url).toContain("context=3");
    expect(r.filesDiff).toEqual(diffPayload);
    expect(r.filesDiffError).toBeNull();
    expect(r.filesDiffLoading).toBe(false);
  });

  it("loadDiff is a no-op when nothing is selected", async () => {
    globalThis.fetch = vi.fn();
    r.filesSelected = null;

    await r.loadDiff();

    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it("loadDiff sets filesDiffError on 404 with a friendly message", async () => {
    r.filesSelected = { cwd: "/repo", path: "foo.py" };
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse({}, { ok: false, status: 404 }));

    await r.loadDiff();

    expect(r.filesDiffError).toMatch(/Diff endpoint not available/);
    expect(r.filesDiffLoading).toBe(false);
  });

  it("loadDiff sets filesDiffError on a 500", async () => {
    r.filesSelected = { cwd: "/repo", path: "foo.py" };
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse({}, { ok: false, status: 500 }));

    await r.loadDiff();

    expect(r.filesDiffError).toMatch(/HTTP 500/);
    expect(r.filesDiffLoading).toBe(false);
  });

  it("loadDiff sets filesDiffError on network error", async () => {
    r.filesSelected = { cwd: "/repo", path: "foo.py" };
    globalThis.fetch = vi.fn().mockRejectedValue(new Error("offline"));

    await r.loadDiff();

    expect(r.filesDiffError).toBe("Network error loading diff");
    expect(r.filesDiffLoading).toBe(false);
  });

  it("closeFileDiff clears all panel state (selection, diff, error, editor toast)", () => {
    r.filesSelected = { cwd: "/x", path: "y" };
    r.filesDiff = { diff: "stuff" };
    r.filesDiffError = "old error";
    r._editorOpenStatus = "Opened";

    r.closeFileDiff();

    expect(r.filesSelected).toBeNull();
    expect(r.filesDiff).toBeNull();
    expect(r.filesDiffError).toBeNull();
    expect(r._editorOpenStatus).toBeNull();
  });
});

describe("visibleFileChanges filter", () => {
  let r;
  beforeEach(() => {
    r = newRoot();
    r.fileChanges = [
      { path: "src/a.py", cwd: "/repo", project: "repo", kind: "modified",
        ts: "2026-05-17T10:00:00Z", session_pids: [1, 2] },
      { path: "README.md", cwd: "/repo", project: "repo", kind: "created",
        ts: "2026-05-17T11:00:00Z", session_pids: [3] },
      { path: "deleted.py", cwd: "/other", project: "other", kind: "deleted",
        ts: "2026-05-17T09:00:00Z", session_pids: [1] },
    ];
    r.filesKindFilter = "All";
    r.filesSearch = "";
    r.filesPidFilter = null;
  });

  it("returns all entries newest-first by default", () => {
    const out = r.visibleFileChanges();
    expect(out.map((c) => c.path)).toEqual(["README.md", "src/a.py", "deleted.py"]);
  });

  it("kind filter narrows to a single kind", () => {
    r.filesKindFilter = "Created";
    const out = r.visibleFileChanges();
    expect(out.map((c) => c.path)).toEqual(["README.md"]);
  });

  it("search filter matches path/project/cwd case-insensitively", () => {
    r.filesSearch = "OTHER";
    const out = r.visibleFileChanges();
    expect(out.map((c) => c.path)).toEqual(["deleted.py"]);
  });

  it("pid filter keeps only changes whose session_pids include the pid", () => {
    r.filesPidFilter = 1;
    const out = r.visibleFileChanges();
    expect(out.map((c) => c.path).sort()).toEqual(["deleted.py", "src/a.py"]);
  });
});

describe("Files tab polling via _onViewChange", () => {
  let r;
  beforeEach(() => {
    r = newRoot();
    vi.useFakeTimers();
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse([]));
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("entering the files view starts a 5s polling timer and runs loadFileChanges immediately", () => {
    expect(r._filesTimer).toBeNull();
    r.view = "files";
    r._onViewChange("files");

    expect(r._filesTimer).not.toBeNull();
    // Immediate call fires.
    expect(globalThis.fetch).toHaveBeenCalledTimes(1);

    // Advance one polling tick.
    vi.advanceTimersByTime(5000);
    expect(globalThis.fetch).toHaveBeenCalledTimes(2);
  });

  it("leaving the files view clears the timer", () => {
    r.view = "files";
    r._onViewChange("files");
    const timerBefore = r._filesTimer;
    expect(timerBefore).not.toBeNull();

    r.view = "dashboard";
    r._onViewChange("dashboard");

    expect(r._filesTimer).toBeNull();
  });
});

describe("jumpToFilesForSession deeplink", () => {
  let r;
  beforeEach(() => {
    r = newRoot();
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse([]));
  });
  afterEach(() => { vi.restoreAllMocks(); });

  it("sets the pid filter, clears search/kind, and switches to the files view", () => {
    r.filesSearch = "stale";
    r.filesKindFilter = "Created";
    r.filesPidFilter = null;

    r.jumpToFilesForSession({ pid: 42 });

    expect(r.filesPidFilter).toBe(42);
    expect(r.filesSearch).toBe("");
    expect(r.filesKindFilter).toBe("All");
    expect(r.view).toBe("files");
  });
});
