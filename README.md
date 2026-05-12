# ClaudeWatch

Local, system-wide monitor for `claude` CLI sessions on macOS. Shows every
running Claude Code in one dashboard with token usage, cost estimates, tool
calls, file changes, git context — and one-click focus / halt / new-session
actions.

No cloud, no API key, no telemetry. Talks only to local processes and local
files.

## What it shows

For every active `claude` process owned by the current user:

- **Location**: iTerm window/tab, tmux session/window/pane, or headless
- **Process**: PID, cwd, duration, CPU%, memory
- **Status**: working / waiting / idle (heuristic from CPU + log activity)
- **Model + permission mode**: from cmdline flags and the conversation log's `permission-mode` entries
- **Token usage**: input / output / cache-read / cache-creation, plus cost estimate per your configured pricing
- **Tool calls**: total, breakdown by tool name, last tool used
- **Thinking** flag if extended thinking is active
- **Files**: cwd changes in the last N minutes (configurable)
- **Git**: branch + dirty status

Plus 24h history of ended sessions, and aggregate stats (token totals, cost,
average duration).

## Actions

- **Focus** the session's iTerm tab (and tmux pane if applicable)
- **Halt** (SIGINT) with confirmation
- **+ New session** in a new iTerm window or tab, with flag whitelist + cwd
  sandboxing

Sending input to an existing session, killing with SIGKILL, and bulk
operations are intentionally not supported (read-only-ish design).

## Install

```bash
git clone https://github.com/<you>/claudewatch.git
cd claudewatch
./scripts/install.sh
```

The script:
1. Creates `.venv/` with Python 3.11+
2. Installs the package editable
3. Symlinks `~/.local/bin/claudewatch` → the venv entrypoint
4. Creates `~/.claudewatch/` for config, history, and logs

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
- Notifications / alerts
- Direct Anthropic API access — costs are computed locally from log usage fields
- Sending input to existing sessions
- Bulk operations (focus/halt/new are one session at a time)

## License

MIT — see [LICENSE](LICENSE).
