# Architecture

> What this monitors and the user-visible feature surface lives in the
> [README](../README.md). This document is for *how* it works.

## Stack

| Layer | Tech |
|---|---|
| Backend | Python 3.10+ — FastAPI + asyncio scheduler |
| Persistence | SQLite via `aiosqlite` (single long-lived connection) |
| Config | TOML at `~/.claudewatch/config.toml` |
| Frontend | Single-page HTML + Alpine.js + Tailwind (CDN) |
| Native tray (optional) | Swift / SwiftUI menu bar app that supervises the Python backend |
| CLI | `typer` (`claudewatch start`, `stop`, `sessions`, …) |
| iTerm bridge | `iterm2` Python API (primary) with AppleScript fallback |

There is no second process for the backend itself — uvicorn + a handful of
asyncio tasks is the entire server. When the native tray app is installed it
spawns the Python backend as a child `Process`, but logically the backend is
the same daemon either way.

## Detector pipeline

The central data flow that turns raw OS state into a `ClaudeSession` snapshot:

```
            scan_claude_processes()             every ~2s, in a thread
                    │  list[ProcInfo]
                    ▼
   ┌────────────────┴────────────────┐
   │                                 │
   ▼                                 ▼
link_pids_to_iterm()           link_pids_to_tmux()
(uses cached iterm_loc_map     (tmux list-panes + descendant walk)
 + iterm_tty_map from a
 separate refresh loop)
   │                                 │
   └─────────────┬───────────────────┘
                 ▼
          parse_log()                   reads ~/.claude/projects/<dashed>/<sid>.jsonl
          (mtime-cached)                → tokens, model, tool stats, subagents,
                 │                        in-flight task, last assistant turn
                 ▼
          get_git_context()             `git branch --show-current` + porcelain
          (10s TTL cache)
                 │
                 ▼
          FilesystemWatcher             `watchfiles` per-cwd, deque per cwd,
          .get_recent(cwd, minutes)     pruned by file_change_retention_minutes
                 │
                 ▼
          ClaudeSession (pydantic)      → SSE broadcast (/api/stream)
                                         → SQLite upsert (active row)
                                         → diff cache (hash) so we only emit
                                           session.updated when something
                                           meaningful actually changed
```

### `scan_claude_processes` (`detectors/process_detector.py`)

Walks `psutil.process_iter` filtering by `claude` command names that belong
to the current user. Parses each cmdline for `--model`, `--session-id`,
`--permission-mode`, etc. (`parse_cmdline`) and returns a list of
`ProcInfo`. Sampled CPU is fed into a `CpuHistory` deque so `infer_status`
can decide whether a session is working / waiting / idle.

### `link_pids_to_iterm` (`detectors/iterm_detector.py`)

Pure function: given a list of claude PIDs and pre-enumerated
`ItermSessionInfo` entries (from `ItermConnectionManager.get_sessions()`),
matches each PID to a `(window_id, tab_id, session_id)` triple. Matches
either directly on iTerm's `jobPid` or by walking up the PID's ancestry (so a
claude running under a shell that is itself the iTerm jobPid still gets
linked).

`ItermConnectionManager` holds **one long-lived WebSocket** to iTerm. Earlier
versions opened a fresh connection every 2 seconds, which on macOS Sonoma+
caused iTerm to briefly steal window focus (#2). The manager additionally:

- caches the last successful enumeration for 30s,
- backs off 10s after a failure before retrying the connect,
- uses split locks so `focus_session` / `send_text` don't serialise behind
  scheduler-driven `get_sessions`.

When the Python API can't link a PID (or isn't installed), `iterm_applescript.link_pids_to_iterm_applescript`
is invoked as a fallback — but only when there are unlinked claude PIDs and
30 seconds have elapsed since the last AppleScript call. AppleScript also
fronts the iTerm window briefly on Sonoma+, so we rate-limit it hard.

### `link_pids_to_tmux` (`detectors/tmux_detector.py`)

Parses `tmux list-panes -a` output, walks each pane's descendants, and
matches the claude PIDs. Yields `(session, window, pane)` per linked PID.
Cheap (~1ms even with many panes) so it runs every tick.

### `parse_log` (`detectors/conversation_log.py`)

Reads the per-session JSONL at `~/.claude/projects/<cwd-as-dashes>/<sid>.jsonl`,
aggregates token usage / cost / tool counts / subagents and surfaces the
in-flight task + last assistant turn. The linker caches each parse by
`(path, mtime)` so an unchanged log is never re-read.

When `--session-id` is not on the cmdline the linker falls back to "freshest
log in this cwd" — see [troubleshooting](troubleshooting.md) for the
implications of that.

### `get_git_context` (`detectors/git_context.py`)

`git branch --show-current` + `git status --porcelain`, with a 10-second TTL
cache in `LinkerState.git_cache` so a tight scan interval doesn't fork git
for every session every tick.

### `FilesystemWatcher` (`detectors/filesystem_watch.py`)

One `watchfiles` watch per active cwd, started/stopped via
`sync_active_cwds()`. Each watcher fills a per-cwd deque of `FileChange`
records; `get_recent(cwd, minutes)` filters by `file_change_retention_minutes`
(default 10). `ignore_patterns` in config keeps `node_modules/`, build dirs,
`.git/` etc. out of the deque.

## Scheduler

`backend/server.py` runs **two** asyncio loops, started from the FastAPI
lifespan.

### `_scheduler_loop` — every `process_scan_interval_seconds` (default 2s)

1. `await build_sessions(...)` — runs the detector pipeline above, reusing
   the cached `iterm_loc_map` / `iterm_tty_map` populated by the iTerm
   refresh loop.
2. `_emit_diffs(...)` — compares the new snapshot to `s.sessions`. Emits
   `session.started` / `session.ended` unconditionally, and `session.updated`
   only when the SHA-256 of the (filtered) serialized session has changed.
   The filter excludes per-tick fields like `duration_seconds`, `cpu_percent`,
   `memory_mb`, `last_activity_at`, `current_task_elapsed_seconds` so we
   don't broadcast a no-op every 2 seconds (#45).
3. `await s.fs_watcher.sync_active_cwds({cwds})` — adds/removes watchers
   based on which cwds currently host an active session.
4. `_maybe_prune(s)` — calls `state.prune()` once an hour from inside the
   loop. No separate timer.

Each iteration is wrapped in a `time.monotonic()` bracket so the metrics
dataclass (below) can record the duration. The exception path bumps
`detector_failures_total` and continues; one failed tick never kills the
loop.

### `_iterm_refresh_loop` — every `iterm_refresh_interval_seconds` (default 5s)

Calls `ItermConnectionManager.get_sessions()` and rebuilds
`s.iterm_loc_map` / `s.iterm_tty_map`. The main scheduler then consumes
those cached maps every tick without re-hitting iTerm itself.

**Why split:** the Python-API call is the expensive and "risky" one. On
macOS Sonoma+, doing it on the same 2s cadence as the process scan was the
underlying cause of issue #2 (focus stealing). A dedicated 5s loop also lets
the main loop run as tightly as the user wants without affecting iTerm
chatter.

Same `time.monotonic()` bracket pattern as the main loop — duration is
folded into `iterm_refresh_duration_ms_sum`, exceptions bump
`iterm_refresh_failures_total`.

## Metrics & observability

`AppState.metrics` is a `Metrics` dataclass populated in-process by the
scheduler loops, SSE generator, and `AppState.broadcast`. There is no
external Prometheus client library — the counters are plain ints/floats and
the `/api/metrics.prom` route hand-renders the text exposition format
(`backend/api/metrics.py`).

Instrumentation points:

| Counter / gauge | Where it's bumped |
|---|---|
| `scheduler_ticks_total` + `scheduler_tick_duration_ms_{sum,max}` | `_scheduler_loop` `finally` block |
| `iterm_refresh_total` + `iterm_refresh_duration_ms_sum` | `_iterm_refresh_loop` `finally` block |
| `iterm_refresh_failures_total` | `_iterm_refresh_loop` exception handler |
| `detector_failures_total` | `_scheduler_loop` exception handler |
| `process_scan_last_count` | end of each successful `_scheduler_loop` body |
| `broadcasts_total` | `AppState.broadcast` (one per fan-out call) |
| `sse_subscribers` (gauge) | incremented at the top of the `/api/stream` generator, decremented in its `finally` so cancellation / disconnect still tracks |
| `started_at` | set once at `AppState.__init__` via `default_factory` |

The Metrics dataclass is reset on every daemon restart — counters are
process-lifetime, not persisted. For long-horizon analytics either scrape
the Prometheus endpoint into a real TSDB or query the SQLite history
(`/api/history`, `/api/history/hourly`, `/api/forecast`) which *is*
persistent.

Two JSON-only derived fields, `scheduler_tick_duration_ms_avg` and
`iterm_refresh_duration_ms_avg`, are computed on each request from the
`_sum / _total` pair. They're omitted from the Prometheus output by design
— promql does that better via `rate()`.

## API surface

All routers are mounted under `/api`. One-liner per route — full schemas
live in [api-reference.md](api-reference.md).

| Router | Routes |
|---|---|
| `sessions` | `GET /sessions`, `GET /sessions/{pid}`, `GET /sessions/{pid}/files`, `GET /sessions/{pid}/log-tail`, `GET /sessions/{pid}/log-stream` (SSE) |
| `actions` | `POST /sessions/new`, `POST /sessions/{pid}/halt`, `POST /sessions/{pid}/focus`, `POST /sessions/{pid}/send-text` |
| `stream` | `GET /stream` (SSE — all session diffs) |
| `health` | `GET /health` |
| `history` | `GET /history`, `GET /stats` |
| `config_api` | `GET /config`, `POST /config`, `GET /pricing`, `POST /pricing` |
| `insights` | `GET /projects`, `GET /history/hourly`, `GET /history/hourly-cost`, `GET /sessions/{pid}/export`, `GET /export.csv` |
| `forecast` | `GET /forecast` (rolling-window cost extrapolation) |
| `metrics` | `GET /metrics` (JSON), `GET /metrics.prom` (Prometheus exposition) |
| `admin` | `GET /admin/status`, `GET /admin/logs`, `POST /admin/prune`, `POST /admin/restart` |
| `files` | `GET /file-changes`, `GET /files/diff`, `POST /files/open` |

Privacy mode (`show_log_text=false`) is enforced in `sessions.py` —
text + tool_use inputs are stripped from both `/log-tail` and `/log-stream`.
Read-only mode (`read_only=true`) disables all of `actions.py`.
Remote control (`/sessions/{pid}/send-text`) is gated by
`config.remote_control.enabled` (opt-in, default False) on top of read-only.
Open-in-editor (`/files/open`) is gated by `config.editor.enabled`
(opt-in, default False).

## State

- **In-memory** — `AppState.sessions: dict[pid, ClaudeSession]`, the source
  of truth for the dashboard. Also `session_hashes` (diff cache),
  `iterm_loc_map` / `iterm_tty_map` (populated by the iTerm refresh loop),
  `sse_queues` (one per connected client), `notified_high_cost_pids`,
  `send_text_rate` (per-PID token bucket for `/send-text`, #88),
  `metrics` (the dataclass above — counters / gauges).
- **SQLite** — `~/.claudewatch/state.db`, opened once at lifespan startup as
  a single long-lived `aiosqlite.Connection` (#19) and closed at shutdown.
  Schema is one table:
  ```sql
  CREATE TABLE sessions (
      pid INTEGER NOT NULL,
      started_at TEXT NOT NULL,
      ended_at TEXT,
      last_seen TEXT NOT NULL,
      cwd TEXT, model TEXT,
      total_tokens INTEGER DEFAULT 0,
      cost_estimate REAL,
      summary_json TEXT,
      PRIMARY KEY (pid, started_at)
  );
  ```
  Active rows are upserted on every diff tick; `ended_at` is filled when the
  scheduler detects a session has disappeared. `prune()` runs hourly from
  inside `_scheduler_loop` and deletes ended rows older than 48 hours.
- **TOML** — `~/.claudewatch/config.toml`, written atomically (`.tmp` →
  `os.replace`) with `chmod 0600`, and the containing dir at `0700` so other
  local users on shared macOS hosts can't read state.db (#40).

## Process supervision (Swift tray)

When ClaudeWatch is installed as the native `.app`, the Swift menu bar app
owns the lifecycle:

- `PythonRunner` spawns `Contents/Resources/python/bin/python3 -m uvicorn
  backend.server:app` via a `Foundation.Process`, captures stderr to the
  popover's "Show backend status" pane, and kills the process on app quit.
- States: `idle → checking → running(pid) | external | failed(reason)`.
  Before spawning it probes `127.0.0.1:7788`; if something already answers
  (e.g. the user is also running `claudewatch start --daemon`) it enters
  `.external` and observes that instance instead of double-spawning.
- The tray app stays running with `LSUIElement=true` (no Dock icon) and
  re-launches the backend if the child exits unexpectedly.

See [mac/README.md](../mac/README.md) for build / packaging / signing
details.

## Safety guarantees

- Bind always `127.0.0.1`. The TrustedHost middleware rejects requests with
  any other Host header (#39 — defence against DNS rebinding).
- New-session: cwd must resolve under `$HOME`; flags pass a strict regex;
  values run a metacharacter blacklist; arguments are passed as an array to
  AppleScript, never interpolated.
- Halt sends SIGINT only. There is no SIGKILL fallback.
- File-open + diff routes both clamp cwd to active-session cwds and resolve
  candidate paths back under cwd before touching disk.
- `read_only = true` disables every action endpoint.
