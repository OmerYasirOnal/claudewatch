# ClaudeWatch

[![CI](https://github.com/OmerYasirOnal/claudewatch/actions/workflows/ci.yml/badge.svg)](https://github.com/OmerYasirOnal/claudewatch/actions/workflows/ci.yml)
[![mac](https://github.com/OmerYasirOnal/claudewatch/actions/workflows/mac-ci.yml/badge.svg)](https://github.com/OmerYasirOnal/claudewatch/actions/workflows/mac-ci.yml)

Local, system-wide monitor for `claude` CLI sessions on macOS. Shows every
running Claude Code in one dashboard with token usage, cost estimates, tool
calls, file changes, git context — and one-click focus / halt / new-session
actions.

No cloud, no API key, no telemetry. Talks only to local processes and local
files.

## Quick links

- [Architecture](docs/architecture.md) — the detector pipeline, two-loop
  scheduler, and where state lives.
- [Troubleshooting](docs/troubleshooting.md) — focus stealing, the
  "headless everywhere" symptom, plan-aware cost, DMG quarantine, …
- [API reference](docs/api-reference.md) — every route, grouped by router.
- [mac/README.md](mac/README.md) — building and packaging the menu bar
  `.app`.
- [Permissions setup](docs/permissions-setup.md) — what macOS grants are
  needed and where to find them.

## What it shows

For every active `claude` process owned by the current user:

**Process**
- PID, cwd, duration, CPU%, memory
- Status: working / waiting / idle (heuristic from CPU + log activity)
- Model + permission mode (from cmdline flags + log)
- Thinking flag, when extended thinking is active

**Location**
- iTerm window/tab, tmux session/window/pane, or `headless`

**Activity**
- Tool calls — total, breakdown by name, last tool used
- Subagent invocations and current in-flight task

**Cost**
- Token usage: input / output / cache-read / cache-creation
- Cost estimate per the configured pricing (hidden when `plan ≠ api`)

**Files**
- Recent file changes inside the session's cwd (configurable retention)
- Per-file `git diff` from the Files tab

**Git**
- Branch + dirty status

**Forecasts & trends**
- Cost forecast — extrapolates spend over the next 24h / 7d / 30d from a
  rolling window of ended sessions (default 24h, configurable per-call)
- Hourly cost trend — bar chart of cost per hour over the trailing 7 days
  (continuous axis, empty hours render as zero)

Plus 24h history of ended sessions and hourly time-series charts, and a
`/api/metrics` endpoint (JSON + Prometheus) for scraping scheduler and
SSE-fan-out counters.

## Screenshots

> Screenshots are tracked separately — drop new PNGs under `docs/img/` and
> reference them here.

- Dashboard overview — `docs/img/dashboard.png`
- Per-session detail panel — `docs/img/detail.png`
- Files diff view — `docs/img/files-diff.png`
- Menu bar tray — `docs/img/tray.png`

## Actions

- **Focus** the session's iTerm tab (and tmux pane if applicable)
- **Halt** (SIGINT) with confirmation
- **+ New session** in a new iTerm window or tab, with flag whitelist + cwd
  sandboxing
- **Send text** to a running session — opt-in via Settings → Remote Control

Killing with SIGKILL and bulk operations are intentionally not supported.

## Customization

- **Dark mode** — a header toggle cycles `light → dark → auto`. `auto`
  follows `prefers-color-scheme`; the choice persists in `localStorage` and
  is applied by an inline `<head>` script before the first paint so there's
  no theme flash on load.
- **Plan-aware $ visibility** — the dashboard hides dollar figures unless
  the configured `plan` is `api` (the only metered tier). Set in Settings →
  Plan; `pro` / `max` / `max_20x` / `team` / `free` keep token counts
  visible but drop cost columns and the forecast card.
- **Card visibility** — the summary cards on the dashboard can each be
  individually hidden via the eye icon; choice persists per browser.

## Install

### Option 1 — Native .app (recommended for end users)

Download the latest `ClaudeWatch.dmg` from
[Releases](https://github.com/OmerYasirOnal/claudewatch/releases), mount it,
and drag `ClaudeWatch.app` into your `Applications` folder. Double-click to
launch — the icon appears in your menu bar.

Bundles a portable Python 3.12 + the backend, so you don't need to install
anything else. macOS 14 (Sonoma) or newer required.

DMGs published from CI with `UNIVERSAL=1` are universal binaries — the same
`.app` runs natively on both Apple Silicon (arm64) and Intel (x86_64). Size
on disk: ~81 MB single-arch / ~157 MB universal. The Swift menu bar binary
is universal in either build; only the bundled Python tree differs.

See [mac/README.md](mac/README.md) for build, install, DMG packaging, and
the `UNIVERSAL=1` build path.

### Option 2 — Python install (for development / CLI)

```bash
git clone https://github.com/OmerYasirOnal/claudewatch.git
cd claudewatch
./scripts/install.sh
```

The script:
1. Creates `.venv/` with Python 3.10+
2. Installs the package editable
3. Symlinks `~/.local/bin/claudewatch` → the venv entrypoint
4. Creates `~/.claudewatch/` for config, history, and logs

Both options can coexist — the menu bar app detects an existing
`claudewatch start --daemon` and observes it instead of double-spawning.

Then grant the macOS permissions described in
[docs/permissions-setup.md](docs/permissions-setup.md).

## Run

```bash
claudewatch start              # foreground (Ctrl+C to stop)
claudewatch start --daemon     # background; PID at ~/.claudewatch/server.pid
claudewatch open               # opens http://127.0.0.1:7788 in browser
claudewatch sessions           # live TUI table
claudewatch status             # health check
claudewatch stop
```

## CLI reference

| Command | Purpose |
|---------|---------|
| `claudewatch start [--daemon]` | Boot the server |
| `claudewatch stop` | Stop the daemon |
| `claudewatch status` | Is it running? how many active sessions? |
| `claudewatch open` | Open dashboard in browser |
| `claudewatch sessions [--once]` | Rich Live table in your terminal |
| `claudewatch info <pid>` | Full JSON detail for a session |
| `claudewatch new <dir>` | Open a new Claude session in iTerm |
| `claudewatch logs` | Tail the server log |
| `claudewatch config` | Open `~/.claudewatch/config.toml` in `$EDITOR` |
| `claudewatch pricing` | Same file — `[pricing]` table lives there |
| `claudewatch uninstall` | Remove `~/.claudewatch/` data |

## Config

`~/.claudewatch/config.toml` is generated on first run with sensible defaults.
Pricing values are placeholders — verify with
[anthropic.com/pricing](https://anthropic.com/pricing).

Privacy defaults: `privacy_mode = true`, `show_log_text = false`. The
`/api/sessions/{pid}/log-tail` endpoint strips text blocks unless you flip
`show_log_text` on.

## Auto-start on login (optional)

```bash
cp scripts/launchd.plist ~/Library/LaunchAgents/com.claudewatch.server.plist
sed -i '' "s|__HOME__|$HOME|g" ~/Library/LaunchAgents/com.claudewatch.server.plist
launchctl load ~/Library/LaunchAgents/com.claudewatch.server.plist
```

## How detection works

See [docs/architecture.md](docs/architecture.md) for the detector pipeline,
[docs/conversation-log-format.md](docs/conversation-log-format.md) for the JSONL
schema this parses, and [docs/troubleshooting.md](docs/troubleshooting.md) for
common issues.

## What it doesn't do

- Cross-machine monitoring
- Linux / Windows
- Conversation content search or message threading
- Direct Anthropic API access — costs are computed locally from log usage fields
- Bulk operations (focus/halt/new are one session at a time)

## License

MIT — see [LICENSE](LICENSE).
