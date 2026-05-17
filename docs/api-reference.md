# API Reference

All routes are mounted under `/api`, served on `http://127.0.0.1:7788`.
Requests with a non-loopback `Host` header are rejected (DNS-rebinding
defence, #39).

Tables: method, path, query/body, success shape, status codes. Full JSON
examples only for the more complex routes.

## Auth / trust model

There is no auth, no CSRF token, no API key. The trust boundary is the
loopback interface itself: the daemon binds only to `127.0.0.1` and the
`TrustedHostMiddleware` rejects any request whose `Host` header is not
`127.0.0.1` or `localhost` (defence-in-depth against DNS rebinding).

Concretely:

- Anyone with shell access to the user account can hit every endpoint.
- Anyone on the network cannot — there is no listening socket on a routable
  interface.
- Mutating endpoints (`actions.*`, `config_api.POST`, `admin.POST`, plus
  `files.open` and `actions.send-text`) are additionally gated by
  `config.read_only`, `config.remote_control.enabled`, and
  `config.editor.enabled` as called out below.

---

## `sessions` — live session list + per-session detail

| Method | Path | Query / body | Success |
|---|---|---|---|
| `GET` | `/api/sessions` | — | `200` — `list[ClaudeSession]` |
| `GET` | `/api/sessions/{pid}` | — | `200` — `ClaudeSession` · `404` if unknown PID |
| `GET` | `/api/sessions/{pid}/files` | `?minutes=int` (default 10) | `200` — `list[FileChange]` |
| `GET` | `/api/sessions/{pid}/log-tail` | `?limit=int` (1-500, default 20) | `200` — `{entries, log_path, privacy_mode}` · 404 if PID unknown |
| `GET` | `/api/sessions/{pid}/log-stream` | — | `200 text/event-stream` — SSE: `snapshot` then `append` per new JSONL line |

- `log-tail` and `log-stream` redact text + tool_use payloads from each
  entry unless `show_log_text=true` (privacy mode is the default).
- `log-tail` reads at most the trailing 5 MB of the JSONL so multi-GB logs
  can't OOM the server (#47).

---

## `actions` — focus / halt / new / send-text

| Method | Path | Body | Success |
|---|---|---|---|
| `POST` | `/api/sessions/new` | `NewSessionRequest` | `200` — `{success, cwd, command}` |
| `POST` | `/api/sessions/{pid}/halt` | — | `200` — `{success, exited}` · 404 / 409 / 403 |
| `POST` | `/api/sessions/{pid}/focus` | — | `200` — `{success}` · 400 / 404 / 409 |
| `POST` | `/api/sessions/{pid}/send-text` | `SendTextRequest` | `200` — `{success, bytes_sent}` · 403 / 413 |

All four return `403 server is in read-only mode` when `read_only=true`.

- `new_session` validates the cwd resolves under `$HOME`, the binary lives
  under `~/.local/bin` or the Claude support dir (or is the literal
  `claude`), and each flag matches `^--[a-z][a-z0-9-]*(=...)?$` with a
  metacharacter blacklist on values.
- `halt` re-verifies the PID is still a claude process before signalling
  (#33), then sends SIGINT and polls for exit up to 5 seconds.
- `focus` prefers the persistent Python-API path (matching on
  `iterm_session_id`), falls back to AppleScript by tty, and for tmux
  sessions follows up with `tmux select-window` + `select-pane`.
- `send-text` is opt-in via `config.remote_control.enabled = true` (403
  otherwise). Caps payload at 4096 chars (413 over).

---

## `stream` — global SSE event bus

| Method | Path | Body | Success |
|---|---|---|---|
| `GET` | `/api/stream` | — | `200 text/event-stream` |

Initial frame is `event: snapshot` with the current `sessions` list. After
that:

- `event: session.started` — `{event, session}`
- `event: session.updated` — `{event, session}` (only on hash change)
- `event: session.ended` — `{event, pid}`
- `event: reconnect-required` — slow client; client must re-establish (#43)
- `:keepalive` line every 15s if the queue is idle

The generator races the per-client queue against `shutdown_event` so
daemon stop wakes immediately (#27).

---

## `health` — diagnostic snapshot

| Method | Path | Success |
|---|---|---|
| `GET` | `/api/health` | `200` — `HealthReport` (`iterm_reachable`, `tmux_reachable`, `log_dir_found`, etc.) |

---

## `history` — ended sessions + aggregate stats

| Method | Path | Query | Success |
|---|---|---|---|
| `GET` | `/api/history` | `?hours=int` (default 24) | `200` — `list[dict]` of ended sessions |
| `GET` | `/api/stats` | — | `200` — `{active, active_tokens, active_cost, sessions_today, tokens_today, cost_today}` |

---

## `config_api` — config + pricing

| Method | Path | Body | Success |
|---|---|---|---|
| `GET` | `/api/config` | — | `200` — full config dict |
| `POST` | `/api/config` | `ConfigUpdate` | `200` — merged config |
| `GET` | `/api/pricing` | — | `200` — `dict[model, PricingEntry]` |
| `POST` | `/api/pricing` | `dict[model, PricingEntry]` | `200` — merged pricing |

`ConfigUpdate` is strict (`extra=forbid`, #41): unknown keys → 422. Allowed
keys are `port`, `read_only`, `privacy_mode`, `show_log_text`, `plan`
(literal: `api|pro|max|max_20x|team|free`), `file_change_retention_minutes`,
`process_scan_interval_seconds`, `iterm_refresh_interval_seconds`,
`ignore_patterns`, `pricing`, `remote_control.{enabled}`,
`editor.{enabled, command}`.

`PricingEntry` is `{input, output, cache_read, cache_write}`, all
`float >= 0`.

---

## `insights` — analytics + export

| Method | Path | Query | Success |
|---|---|---|---|
| `GET` | `/api/projects` | — | `200` — `list[ProjectRollup]` |
| `GET` | `/api/history/hourly` | `?hours=int` (1-168, default 24) | `200` — `{bins: list[HourlyBin]}` |
| `GET` | `/api/history/hourly-cost` | `?hours=int` (1-720, default 168) | `200` — `{hours, bins, total_cost_usd}` |
| `GET` | `/api/sessions/{pid}/export` | — | `200 application/json` — full session as attachment |
| `GET` | `/api/export.csv` | `?days=int` (1-30, default 7) | `200 text/csv` |

### `GET /api/projects` example

```json
[
  {
    "cwd": "/Users/me/code/claudewatch",
    "active_sessions": 1,
    "sessions_24h": 4,
    "total_tokens_24h": 123456,
    "total_cost_24h": 1.234567,
    "last_active_at": "2026-05-17T10:30:00Z"
  }
]
```

Combines active sessions + the last 24h of ended sessions, deduped by
`cwd`, sorted by `total_cost_24h` desc.

Hourly bins emit one entry per hour (oldest first) with
`{hour, sessions_started, tokens, cost}`. Tokens/cost are attributed at
session end.

### `GET /api/history/hourly-cost` example

```json
{
  "hours": 168,
  "total_cost_usd": 42.317,
  "bins": [
    {"hour_start": "2026-05-10T00:00:00+00:00", "cost_usd": 0.0,   "session_count": 0},
    {"hour_start": "2026-05-10T01:00:00+00:00", "cost_usd": 1.234, "session_count": 2}
  ]
}
```

One entry per hour over the trailing window (default 7 days, max 30 days),
oldest first. Empty hours appear as zero-cost zero-session bins so the
frontend can render a continuous x-axis without gap handling. Cost is
attributed at session end (matching `/api/history/hourly`).

---

## `forecast` — rolling-window cost extrapolation

| Method | Path | Query | Success |
|---|---|---|---|
| `GET` | `/api/forecast` | `?window_hours=int` (1-720, default 24) | `200` — `ForecastResponse` |

```json
{
  "window_hours": 24,
  "observed_cost_usd": 1.231560,
  "observed_session_count": 7,
  "hourly_rate_usd": 0.051315,
  "projection_24h_usd": 1.231560,
  "projection_7d_usd": 8.620920,
  "projection_30d_usd": 36.946800,
  "as_of": "2026-05-17T10:30:00+00:00"
}
```

- Sums `cost_estimate` over the last `window_hours` of ended sessions in
  the SQLite history. Active (still-running) sessions are excluded — cost
  is only final at session end.
- `hourly_rate_usd = observed_cost_usd / window_hours`. Projections are
  `hourly_rate × {24, 168, 720}`. Flat rolling-window average, not an EMA;
  tune the smoothing by widening `window_hours`.
- When the SQLite state isn't reachable (early-boot or shutdown race), the
  endpoint returns a fully-zeroed payload with `as_of` populated rather
  than a 503 — the UI degrades gracefully on a fresh install.
- `window_hours` is clamped to `[1, 720]` by FastAPI's `Query(ge=1, le=720)`.

---

## `metrics` — internal counters (JSON + Prometheus)

| Method | Path | Success |
|---|---|---|
| `GET` | `/api/metrics` | `200 application/json` — counters dict |
| `GET` | `/api/metrics.prom` | `200 text/plain; version=0.0.4` — Prometheus exposition |

Both routes snapshot the `AppState.metrics` dataclass populated by the
scheduler loops in `backend/server.py`. No auth — same trust model as
`/api/admin/status`.

### `GET /api/metrics` example

```json
{
  "scheduler_ticks_total": 12345,
  "scheduler_tick_duration_ms_sum": 184230.5,
  "scheduler_tick_duration_ms_max": 412.8,
  "scheduler_tick_duration_ms_avg": 14.92,
  "iterm_refresh_total": 4938,
  "iterm_refresh_duration_ms_sum": 23104.1,
  "iterm_refresh_duration_ms_avg": 4.68,
  "iterm_refresh_failures_total": 2,
  "broadcasts_total": 18021,
  "sse_subscribers": 1,
  "detector_failures_total": 0,
  "process_scan_last_count": 3,
  "started_at": "2026-05-17T08:00:00Z",
  "uptime_seconds": 9000
}
```

Field semantics:

- `scheduler_ticks_total` / `iterm_refresh_total` — counter, one per loop
  iteration. The `_sum` / `_max` pairs are the cumulative and peak
  duration in milliseconds (measured with `time.monotonic()` so NTP jumps
  can't corrupt them).
- `*_avg` fields are derived (`sum / total`) and only present on the JSON
  route — Prometheus prefers to compute averages with `rate(_sum) / rate(_total)`.
- `iterm_refresh_failures_total` / `detector_failures_total` — counter,
  bumped on any exception in the corresponding loop.
- `broadcasts_total` — counter, one per SSE fan-out call (`AppState.broadcast`).
- `sse_subscribers` — **gauge**. Incremented when an `/api/stream`
  generator starts, decremented when it exits.
- `process_scan_last_count` — **gauge**. Number of `ClaudeSession`s
  emitted by the most recent scheduler tick.
- `started_at` / `uptime_seconds` — when the daemon's `AppState` was
  constructed (UTC ISO 8601) and the seconds since then.

### `GET /api/metrics.prom` example

```
# HELP claudewatch_scheduler_ticks_total Number of scheduler tick iterations
# TYPE claudewatch_scheduler_ticks_total counter
claudewatch_scheduler_ticks_total 12345
# HELP claudewatch_scheduler_tick_duration_ms_sum Cumulative scheduler tick duration in milliseconds
# TYPE claudewatch_scheduler_tick_duration_ms_sum counter
claudewatch_scheduler_tick_duration_ms_sum 184230.5
# HELP claudewatch_scheduler_tick_duration_ms_max Maximum observed scheduler tick duration in milliseconds
# TYPE claudewatch_scheduler_tick_duration_ms_max gauge
claudewatch_scheduler_tick_duration_ms_max 412.8
...
# HELP claudewatch_uptime_seconds Daemon uptime in seconds
# TYPE claudewatch_uptime_seconds gauge
claudewatch_uptime_seconds 9000
```

Each metric is prefixed `claudewatch_`. Counters end in `_total`; gauges
don't (matches the Prometheus naming convention). The `_avg` JSON helpers
are intentionally not exported — Grafana / promql users should
`rate(_sum[5m]) / rate(_total[5m])` instead. Trailing newline included per
the Prom text format spec.

Scrape example:

```yaml
# prometheus.yml
scrape_configs:
  - job_name: claudewatch
    metrics_path: /api/metrics.prom
    static_configs:
      - targets: ['127.0.0.1:7788']
```

---

## `admin` — daemon status, log tail, prune, restart

All admin routes are mounted at `/api/admin`. Write endpoints respect
`config.read_only` (returns 403 when true).

| Method | Path | Query | Success |
|---|---|---|---|
| `GET` | `/api/admin/status` | — | `200` — version, uptime, DB + log stats, scheduler timing, iTerm liveness |
| `GET` | `/api/admin/logs` | `?lines=int` (1-1000, default 100), `?grep=str` (max 200 chars) | `200` — `{path, size_bytes, lines, truncated}` |
| `POST` | `/api/admin/prune` | `?hours=int` (1 to 24·365·100, default 48) | `200` — `{rows_deleted}` · 403 · 503 |
| `POST` | `/api/admin/restart` | `?confirm=bool` (must be true) | `202` — `{restart_initiated: true}` · 400 · 403 |

- `status` reports `version`, `uptime_seconds`, `started_at`, `pid`,
  `python_version`, `active_sessions`, `history_rows`,
  `history_db_size_bytes`, `log_file` (path) + `log_file_size_bytes`,
  scheduler intervals + `last_prune_at` + `next_prune_in_seconds`, and the
  iTerm manager's `connected` + `last_error_at`.
- `logs` reads at most the trailing 1 MB of `~/.claudewatch/logs/server.log`,
  then tails `lines` after optional substring `grep`. `truncated: true`
  means the read window was smaller than the file.
- `prune` triggers `State.prune(hours=...)` immediately and returns the
  delta in row count.
- `restart` SIGTERMs the daemon after a 100 ms delay so uvicorn can flush
  the response body. Useful under launchd (`KeepAlive=true`) or the
  bundled `.app`'s `PythonRunner` (which sees the exit and re-spawns).
  `?confirm=true` is required so a stray `curl` can't drop the daemon.

---

## `files` — cross-session file feed, per-file diff, open-in-editor

| Method | Path | Query / body | Success |
|---|---|---|---|
| `GET` | `/api/file-changes` | `?minutes=int` (1-120, default 10) | `200` — flat deduped list, capped at 500 |
| `GET` | `/api/files/diff` | `?cwd=str&path=str&context=int` (0-50, default 3) | `200` — `DiffResult` · 400 / 504 / 500 |
| `POST` | `/api/files/open` | `OpenFileRequest` | `200` — `{success, command, path, cwd}` · 403 / 400 / 404 |

### `GET /api/files/diff` example

```json
{
  "cwd": "/Users/me/code/claudewatch",
  "path": "backend/server.py",
  "is_git": true,
  "tracked": true,
  "diff": "diff --git a/backend/server.py b/backend/server.py\n@@ -1 +1 @@\n-...\n+...\n",
  "stat": " backend/server.py | 2 +-",
  "untracked_preview": null
}
```

- `cwd` must match an *active* session's cwd. The endpoint rejects any
  other path (400) so it can't be used to peek at arbitrary directories.
- `path` is rejected if it starts with `/` or contains `..` segments;
  resolved candidates must remain inside `cwd` and under `$HOME`.
- Non-git cwds → `is_git: false`, plain head-of-file preview (max 64 KB).
- Untracked files in a git cwd → `tracked: false` + preview.
- `git diff` has a 5 s timeout (504 on overflow).

`POST /api/files/open` is opt-in via `config.editor.enabled = true`. The
configured `command` is matched against `^[A-Za-z0-9_/.\- ]+$` server-side
(belt-and-braces; `ConfigUpdate` enforces the same pattern), then split on
whitespace and spawned with `subprocess.Popen(argv, start_new_session=True)`
so the editor survives daemon exit.
