# API Reference

All routes are mounted under `/api`, served on `http://127.0.0.1:7788`.
Requests with a non-loopback `Host` header are rejected (DNS-rebinding
defence, #39).

Tables: method, path, query/body, success shape, status codes. Full JSON
examples only for the more complex routes.

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
