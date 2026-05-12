# ClaudeWatch v0.2 — Design Spec

**Date:** 2026-05-12
**Author:** Refined from user spec, schema-verified against live `~/.claude/projects/` logs.
**Target:** macOS Sequoia 15+ only.

---

## 1. Purpose

A local, single-user web dashboard that shows every `claude` CLI process running on the user's Mac with enough metadata to answer at a glance:

- Where is each session running? (iTerm tab, tmux pane, or headless)
- What is it doing? (model, current activity, tool calls, files changed)
- What has it cost? (token usage + estimated $)
- Should I interrupt it? (focus the tab, halt, or spawn a new one)

No cloud, no API key, no telemetry. Talks only to local processes and local files.

---

## 2. Scope

### In scope (v0.2)
- Detection of all `claude` processes owned by the current user
- Location classification: iTerm window/tab, tmux session/window/pane, or headless
- Conversation metadata enrichment: model, token usage, cost, tool calls, permission mode, message count
- Filesystem activity: files changed in cwd in the last N minutes
- Git context: branch + dirty state for each cwd
- Actions: Focus tab, Halt (Ctrl+C with confirm), New session (new iTerm window/tab)
- 24-hour ephemeral history of ended sessions
- TUI (`claudewatch sessions`) and CLI helpers

### Out of scope (v0.2)
- Cross-machine monitoring
- Linux/Windows support
- Conversation content search
- Notifications / alerts
- Mobile companion
- Direct Anthropic API integration (only parses local logs)
- Session templates / preset library
- Cost budgeting / alerts
- Sending commands to existing sessions (read-only principle)

---

## 3. Schema Findings (Verified 2026-05-12)

Inspected `~/.claude/projects/-Users-me-Projects-example/56fdc957-…jsonl`:

**Folder layout:** `~/.claude/projects/<cwd-with-slashes-as-dashes>/<sessionId>.jsonl`
- E.g., cwd `/Users/me/Projects/example` → folder `-Users-me-Projects-example`
- Session UUID matches the `sessionId` field on every entry inside the file

**Entry types seen:**
`last-prompt`, `permission-mode`, `attachment`, `file-history-snapshot`, `user`, `assistant`, `system`, `ai-title`

**Common top-level fields on every entry:**
- `type`, `sessionId`, `cwd`, `gitBranch`, `version` (Claude CLI version), `timestamp`, `uuid`, `parentUuid`

**Assistant entry shape:**
```
type: "assistant"
message:
  model: "claude-opus-4-7"
  content: [ {type: "thinking"|"text"|"tool_use", ...} ]
  stop_reason, stop_sequence, stop_details
  usage:
    input_tokens, output_tokens
    cache_creation_input_tokens, cache_read_input_tokens
    server_tool_use, service_tier
```

**Permission-mode entry:**
```
type: "permission-mode"
permissionMode: "auto" | "default" | "plan" | …
sessionId: <uuid>
```

**Implications for design:**
- PID↔conversation linkage uses **direct cwd match** (no need for mtime heuristics): given a Claude process with `cwd = /a/b/c`, hash to `-a-b-c`, glob `*.jsonl` in that folder, pick the file whose newest entry has the latest timestamp matching the process's recent activity window.
- The actual usage field name is `cache_read_input_tokens` (not `cache_read_tokens` as the original spec drafted). Same for `cache_creation_input_tokens`.
- `permission-mode` entries give the authoritative source; cmdline `--dangerously-skip-permissions` is a secondary signal.
- `gitBranch` is in the log — but we still query git locally to detect dirty state.
- `version` field tells us which Claude CLI built the log; useful for future schema-version handling.

---

## 4. Architecture

### 4.1 Tech stack
- **Backend:** Python 3.11+ (3.12 confirmed on user's system). FastAPI, Uvicorn, psutil, iterm2, watchfiles, GitPython, aiosqlite.
- **Frontend:** Single `index.html` + Tailwind (CDN) + Alpine.js + native EventSource for SSE.
- **Bind:** `127.0.0.1:7788` (configurable). Localhost-only — never bind 0.0.0.0.
- **Persistence:** SQLite at `~/.claudewatch/state.db` for 24-hour history. Config at `~/.claudewatch/config.toml`.

### 4.2 Process model
One long-lived `uvicorn` process. Inside it:

| Task | Frequency | Purpose |
|------|-----------|---------|
| Process scanner | 2 s | psutil scan, cmdline parse, status inference |
| iTerm linker | 5 s | Cross-reference jobPid from iTerm Python API |
| tmux linker | 5 s | `tmux list-panes -a` + walk pane children |
| Conversation log scanner | 3 s | Auto-detect logs, parse new entries since last offset |
| Git context refresh | 10 s | Per cwd, run `git branch --show-current` + `git status --porcelain` |
| Filesystem watcher | continuous | watchfiles per active cwd, ignore patterns applied |
| State persistor | on event | Write session updates to SQLite + broadcast SSE |

### 4.3 Directory layout

```
backend/
  __init__.py
  server.py                      # FastAPI app entry, lifespan, task scheduler
  models.py                      # Pydantic schemas (see §5)
  config.py                      # TOML loader + defaults
  state.py                       # SQLite wrapper (sessions, history)
  pricing.py                     # Token → USD calculator
  permissions.py                 # macOS permission probes + remediation hints
  detectors/
    process_detector.py          # psutil + cmdline parse + status inference
    iterm_detector.py            # iterm2 Python API client
    tmux_detector.py             # subprocess + parse pane list
    conversation_log.py          # JSONL parser, incremental tail
    filesystem_watch.py          # watchfiles per-cwd manager
    git_context.py               # git branch + porcelain
    linker.py                    # Aggregates: PID → log → location → cwd context
  api/
    sessions.py                  # GET /api/sessions, /api/sessions/{pid}
    actions.py                   # POST /api/sessions/{pid}/{focus,halt}, /api/sessions/new
    stream.py                    # GET /api/stream (SSE)
    config_api.py                # GET/POST /api/config, /api/pricing
    health.py                    # GET /api/health
    history.py                   # GET /api/history, /api/stats
  applescript/
    focus_iterm.applescript
    new_iterm_window.applescript
    new_iterm_tab.applescript
frontend/
  index.html
  app.js                         # Alpine root component
  styles.css                     # Custom on top of Tailwind CDN
scripts/
  install.sh
  start.sh
  stop.sh
  uninstall.sh
  launchd.plist
  claudewatch                    # CLI entry (calls into backend.cli)
docs/
  permissions-setup.md
  architecture.md
  conversation-log-format.md
  troubleshooting.md
tests/
  test_process_detector.py
  test_conversation_log.py
  test_linker.py
  test_pricing.py
  test_api.py
pyproject.toml
README.md
LICENSE
CLAUDE.md
```

### 4.4 Detection pipeline

```
                ┌─────────────────────┐
                │  Process scanner     │  every 2s
                │  (psutil + cmdline)  │
                └──────────┬──────────┘
                           │  list[ProcInfo]
                           ▼
   ┌───────────────────────┴────────────────────────┐
   │                                                │
   ▼                                                ▼
┌──────────────┐                          ┌────────────────────┐
│ iTerm linker │                          │ tmux linker        │
│ (jobPid map) │                          │ (pane_pid + kids)  │
└──────┬───────┘                          └────────┬───────────┘
       │ location enrichment                       │
       └────────────────┬──────────────────────────┘
                        ▼
              ┌──────────────────────┐
              │ Conversation log    │
              │ linker (cwd-hash)    │
              └──────────┬───────────┘
                         │ + usage, tools, model, perm mode
                         ▼
              ┌──────────────────────┐
              │ Filesystem watcher   │
              │ + Git context        │
              └──────────┬───────────┘
                         │
                         ▼
              ┌──────────────────────┐
              │ ClaudeSession (full) │
              │  → SQLite + SSE      │
              └──────────────────────┘
```

### 4.5 Status inference rules

| Status | Trigger |
|--------|---------|
| `working` | CPU > 5% averaged over last 10 s |
| `waiting` | CPU < 1% for 30 s **and** conversation log shows last entry is user-type (awaiting response not yet written) OR cmdline has no recent write |
| `idle` | CPU < 1% for 5 min |
| `ended` | Process gone; flush to history table |

Status is a heuristic. Display includes the underlying CPU/last-activity figures so the user can sanity-check.

---

## 5. Data Model (Pydantic)

```python
class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    @property
    def total_tokens(self) -> int: ...
    cost_estimate_usd: float | None = None

class ToolCallStats(BaseModel):
    total: int = 0
    breakdown: dict[str, int] = {}   # tool name → count
    last_used: str | None = None
    last_used_at: datetime | None = None

class FileChange(BaseModel):
    path: str               # cwd-relative
    kind: Literal["created", "modified", "deleted"]
    ts: datetime

class GitContext(BaseModel):
    branch: str | None
    is_dirty: bool
    modified_count: int

class ClaudeSession(BaseModel):
    # Process
    pid: int
    cwd: str
    started_at: datetime
    duration_seconds: int
    cpu_percent: float
    memory_mb: float
    status: Literal["working", "waiting", "idle", "ended"]

    # Location
    location_type: Literal["iterm", "tmux", "headless"]
    iterm_window_id: int | None = None
    iterm_tab_id: int | None = None
    iterm_session_id: str | None = None
    iterm_tab_title: str | None = None
    tmux_session: str | None = None
    tmux_window: str | None = None
    tmux_pane: str | None = None

    # Activity
    last_activity_at: datetime
    last_output_preview: str | None = None     # only if privacy mode off

    # Enriched from conversation log
    model: str | None = None
    cli_version: str | None = None
    conversation_id: str | None = None
    conversation_log_path: str | None = None
    message_count: int = 0
    usage: TokenUsage | None = None
    thinking_enabled: bool | None = None
    permission_mode: str | None = None         # "auto", "default", "plan", "dangerously-skip", …
    extra_flags: list[str] = []
    tool_calls: ToolCallStats = ToolCallStats()

    # Filesystem
    recent_file_changes: list[FileChange] = []
    git: GitContext | None = None
```

---

## 6. API Surface

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/sessions` | List active sessions |
| GET | `/api/sessions/{pid}` | Full detail |
| GET | `/api/sessions/{pid}/files` | Recent file changes (last N min) |
| GET | `/api/sessions/{pid}/log-tail?limit=20` | Last N parsed conversation entries (privacy-respecting) |
| GET | `/api/sessions/{pid}/output?lines=40` | Last N lines of session stdout (privacy mode must allow) |
| POST | `/api/sessions/{pid}/focus` | AppleScript focus iTerm tab + tmux pane select |
| POST | `/api/sessions/{pid}/halt` | Send SIGINT (Ctrl+C) — confirmation enforced client-side |
| POST | `/api/sessions/new` | Open new iTerm window/tab and run `claude` |
| GET | `/api/history` | Last 24h ended sessions |
| GET | `/api/stats` | Aggregates: count, today_total, today_tokens, today_cost, avg_duration |
| GET | `/api/health` | Detector + permission status |
| GET | `/api/pricing` / POST | Read / write pricing config |
| GET | `/api/config` / POST | Read / write config |
| GET | `/api/stream` | SSE: `session.started`, `session.updated`, `session.ended`, `session.file_changed`, `session.tool_used`, `session.usage_updated` |

### 6.1 `POST /api/sessions/new` body

```json
{
  "cwd": "/Users/me/Projects/x",
  "window_type": "new-window" | "new-tab",
  "flags": ["--dangerously-skip-permissions", "--model", "claude-opus-4-7"],
  "command": "claude"
}
```

**Sanitization:**
- `cwd` must exist and resolve under the user's home directory tree.
- `command` must equal `claude` or an absolute path inside `/Users/<user>/.local/bin/`.
- Each `flag` must match `^--[a-z][a-z0-9-]*(=[A-Za-z0-9._/=-]+)?$` or be a value following a flag (whitelist of value-accepting flags: `--model`, `--system-prompt-file`).
- No shell metacharacters anywhere. Args passed as an array to AppleScript via `do script` template, not string interpolation.

### 6.2 Focus contract

| location_type | Behavior |
|---------------|----------|
| `iterm` | AppleScript: activate iTerm, `select` the window + tab |
| `tmux` | First focus the host iTerm window, then `tmux select-window` + `select-pane` |
| `headless` | Return 400 with explanation; no focus available |

### 6.3 Halt contract

`os.kill(pid, signal.SIGINT)`. If process survives 5 s, return 409 with "process did not exit cleanly". No SIGKILL fallback.

---

## 7. Permissions & Privacy

| Capability | Permission required |
|------------|---------------------|
| Process scanning | None (psutil read-only) |
| iTerm session enumeration | iTerm2 Settings → General → Magic → Enable Python API |
| Focus / new-session | System Settings → Privacy & Security → Automation → Terminal / Python → iTerm |
| Conversation log reading | None (files in `~/.claude/projects/`) |
| Filesystem watcher | None |

Privacy defaults:
- `read_only = false`
- `privacy_mode = true` (output bodies hidden; only metadata shown)
- `show_log_text = false` (conversation content opted-out by default)

`/api/health` enumerates each detector + permission; frontend renders fix links.

Read-only override: if `read_only = true` in config, halt/focus/new-session endpoints return 403.

---

## 8. Frontend

Single-page Alpine app. Sections:

1. **Top bar:** brand, version, detector health badges, refresh-rate selector, privacy toggle, `+ New session` button, settings cog.
2. **Stats row:** active count, today count, today tokens, today cost, avg duration.
3. **Filter chips:** All / iTerm / Tmux / Headless / Working / Idle / High-cost.
4. **Session cards:** layout per §4.5 of the original prompt. Status dot + permission badge.
5. **Detail modal:** click PID. Shows full metadata, tool-call breakdown bar, recent files, git diff stat, conversation log path, usage timeline.
6. **New-session modal:** cwd picker (`Recent cwds` dropdown from history table), window-type radio, flag checkboxes, custom flag input (sanitized).
7. **History route:** `/history` — table view of last-24h ended sessions with totals.
8. **Settings route:** `/settings` — refresh rate, privacy mode, auto-start, per-detector enable, port, pricing editor, ignore patterns, file-change retention.

SSE keeps cards live; full re-fetch happens only on tab focus.

---

## 9. Pricing Config

Default `~/.claudewatch/config.toml`:

```toml
port = 7788
read_only = false
privacy_mode = true
show_log_text = false
file_change_retention_minutes = 10
process_scan_interval_seconds = 2
ignore_patterns = [".git/", "node_modules/", "__pycache__/", ".venv/", ".DS_Store"]

[pricing."claude-opus-4-7"]
input = 15.00
output = 75.00
cache_read = 1.50
cache_write = 18.75

[pricing."claude-sonnet-4-6"]
input = 3.00
output = 15.00
cache_read = 0.30
cache_write = 3.75

[pricing."claude-haiku-4-5"]
input = 1.00
output = 5.00
cache_read = 0.10
cache_write = 1.25
```

Cost = `(input_tokens × input + output_tokens × output + cache_read × cache_read + cache_creation × cache_write) / 1_000_000`. Unknown model → `cost = null`. Surface a "verify pricing at anthropic.com/pricing" notice in settings.

---

## 10. CLI

```
claudewatch start [--daemon]
claudewatch stop
claudewatch status
claudewatch open                # opens browser
claudewatch sessions            # rich-based TUI, htop style
claudewatch info <pid>
claudewatch new <dir> [-- claude-flags]
claudewatch logs                # tail server log
claudewatch config              # opens config in $EDITOR
claudewatch pricing             # opens pricing in $EDITOR
claudewatch uninstall
```

Entry installed via `pyproject.toml [project.scripts]`.

---

## 11. Implementation Phases

A. **Foundation** — pyproject, FastAPI skeleton, `process_detector` w/ cmdline parse.
B. **Location** — tmux + iTerm detectors, health endpoint.
C. **Conversation log + linker + pricing** — JSONL parser, cwd-hash linker, cost calc.
D. **Filesystem + git** — watchfiles per-cwd, git context.
E. **API layer** — endpoints, SQLite history, SSE.
F. **Actions** — focus, halt, new-session AppleScripts + sanitization.
G. **Frontend** — cards, modals, settings, history.
H. **CLI + install** — script entries, install.sh, launchd plist, TUI.
I. **Polish + docs + first tag**.

Exit gates per phase per original §7 spec, plus updated criteria for cwd-direct linkage in C.

---

## 12. Exit Criteria (Acceptance)

### Functional
- Detect all active `claude` processes within 2 s
- iTerm / tmux / headless categorized correctly
- Conversation log linked via direct cwd-hash match; token + tool stats visible within 5 s of new log entry
- Filesystem changes reflected within 2 s
- Git branch + dirty status accurate
- Focus / Halt / New-session all work end-to-end
- Privacy mode default ON; output hidden

### Quality
- Missing permissions surface a fix link in UI
- Conversation log format drift handled gracefully (degrade to process-only view)
- 10+ active sessions: SSE CPU < 1 %
- Backend crash → frontend shows error, doesn't blank
- Shell-injection sanitization unit-tested

### Repo
- README with screenshots, quick-start, FAQ
- `docs/permissions-setup.md`, `conversation-log-format.md`, `troubleshooting.md`
- MIT LICENSE
- Clean install on a fresh Mac in < 5 min
- Tag `v0.2.0` on a green commit

---

## 13. Risks / Assumptions

| Risk | Mitigation |
|------|-----------|
| iTerm2 Python API permission not granted | Permission probe + clear remediation banner; degrade to "iTerm not linked" gracefully |
| Conversation log schema changes in future Claude CLI versions | `version` field is recorded; parser uses `.get(...)` everywhere; falls back to process-only metadata on unknown shapes |
| Anthropic pricing changes | Config-editable; default values clearly marked "verify with Anthropic" |
| Many concurrent sessions exhaust watchfiles | Cap concurrent watchers (config); reuse watcher when multiple PIDs share cwd |
| AppleScript can't always identify iTerm session by pid | iterm2 Python API gives us session UUIDs; we store that primary, fall back to window/tab id |

---

## 14. Non-Goals (Reaffirmed)

- No bulk operations beyond "open one new session"
- No sending input to a running session (no send-keys)
- No SIGKILL
- No cross-user visibility
- No remote dashboards or shareable links
