# Architecture

```
                      ┌────────────────────────┐
                      │  Process scanner        │ every 2 s
                      │  (psutil + cmdline)     │
                      └──────────┬──────────────┘
                                 │ list[ProcInfo]
                                 ▼
        ┌────────────────────────┴─────────────────────────┐
        │                                                  │
        ▼                                                  ▼
 ┌──────────────┐                                  ┌────────────────┐
 │ iTerm linker │                                  │ tmux linker     │
 │ (jobPid map) │                                  │ (pane→kids)     │
 └──────┬───────┘                                  └────────┬────────┘
        │                                                   │
        └─────────────────┬─────────────────────────────────┘
                          ▼
                ┌──────────────────────┐
                │ Conversation log    │
                │  parser + linker     │
                └──────────┬───────────┘
                           │
                           ▼
                ┌──────────────────────┐
                │ Filesystem watcher    │
                │  + Git context        │
                └──────────┬────────────┘
                           ▼
                ┌──────────────────────┐
                │ ClaudeSession (full)  │  ──► SQLite (24h) ──► /api/history
                │                       │  ──► SSE broadcast ──► dashboard
                └───────────────────────┘
```

## Single FastAPI process

`backend/server.py` owns one long-lived `AppState` and starts a background
`_scheduler_loop` task in its lifespan. Every 2 seconds (configurable) it:

1. Calls `build_sessions()` which runs the detector pipeline
2. Diffs against the previous snapshot to emit `session.started`,
   `session.updated`, `session.ended` events on SSE
3. Upserts active sessions into SQLite; marks ended sessions
4. Reconciles which cwds the filesystem watcher should watch

There is no second process — uvicorn + a few asyncio tasks is the whole
server.

## Detector files

Each detector file has a single responsibility and is testable in isolation:

- `process_detector.py` — `scan_claude_processes()`, `parse_cmdline()`,
  `infer_status()`, `CpuHistory`
- `iterm_detector.py` — async iterm2 client, ancestor walk for shell-hosted PIDs
- `tmux_detector.py` — `tmux list-panes -a` parsing + descendant walk
- `conversation_log.py` — `parse_log()`, cwd→folder hashing, log dir probing
- `filesystem_watch.py` — per-cwd watcher manager with retention pruning
- `git_context.py` — `git branch --show-current` + `status --porcelain`
- `linker.py` — pipeline aggregator with mtime + ttl caches

## API surface

Routers map to clear resources. Privacy mode is enforced in
`sessions.py::get_log_tail`; read-only mode is enforced in `actions.py` for
focus/halt/new-session.

## State

- **In-memory:** `AppState.sessions: dict[pid, ClaudeSession]` — source of truth for the dashboard
- **SQLite:** `~/.claudewatch/state.db` — 24-hour rolling history of ended sessions, plus active session snapshots for the stats endpoint
- **TOML:** `~/.claudewatch/config.toml` — user-editable config; rewritten on POST to `/api/config` or `/api/pricing`

## Safety guarantees

- Bind always `127.0.0.1`. No way to bind 0.0.0.0 short of editing the source.
- New-session: cwd must resolve under `$HOME`; flags pass a strict regex; values run a metacharacter blacklist; arguments are passed as an array to AppleScript, never interpolated.
- Halt sends SIGINT only. There is no SIGKILL fallback.
- `read_only = true` disables all action endpoints.
