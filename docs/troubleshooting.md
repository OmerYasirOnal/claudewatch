# Troubleshooting

## All sessions show as `headless`

iTerm2 Python API is disabled or not granted. See
[permissions-setup.md](permissions-setup.md). Sessions are still detected — you
just lose the tab/window linkage and Focus action.

## Token usage / cost shows "—"

The session's conversation log wasn't found. Causes:

- `~/.claude/projects/` doesn't exist (you haven't run Claude Code on this Mac yet)
- The session was started outside the user's home directory tree
- A custom log directory is configured in Claude Code that we don't probe

Verify with `claudewatch info <pid>` — if `conversation_log_path` is null,
that's the gap.

## Two sessions share the same conversation stats

The cmdline didn't include `--resume <sessionId>` or `--session-id`, so the
parser fell back to "freshest log in this cwd". If multiple `claude` processes
share a cwd, they'll all match the same file. The newer Claude CLI almost
always sets one of those flags when resuming, but headless agent SDK scripts
sometimes don't.

## `claudewatch start --daemon` exits but no server appears

Check `~/.claudewatch/logs/server.log` — most likely cause is a stale entry in
`~/.claudewatch/server.pid`. Remove it and retry.

## Focus action fails with "Not authorized to send Apple events"

Grant Automation permission for Terminal (or whichever app is hosting `claudewatch`):
**System Settings → Privacy & Security → Automation → iTerm**.

## Halt sends SIGINT but Claude keeps running

ClaudeWatch never escalates beyond SIGINT. If the session is stuck in a
non-interruptible call, you'll need to terminate the host tab/process
yourself.

## "iTerm not reachable" but iTerm is open

- Make sure the API is enabled (Settings → General → Magic)
- Restart iTerm after enabling
- If the auth setting is "Confirm each time", iTerm pops a confirmation
  every time `claudewatch` reconnects (every detection cycle if the
  connection drops). Set "Full Access" if that's annoying.

## CPU usage of ClaudeWatch itself feels high

The default scan interval is 2 s. Bump it up:

```toml
# ~/.claudewatch/config.toml
process_scan_interval_seconds = 5
```

Filesystem watcher activity also rises with active cwds — large repos that
re-build often (`node_modules`, build dirs) are filtered by default. Extend
`ignore_patterns` if needed.

## Pricing shows wrong numbers

The defaults are placeholders. Edit `[pricing]` in
`~/.claudewatch/config.toml` and verify against
[anthropic.com/pricing](https://anthropic.com/pricing). Unknown models get
`cost = null` (rendered as `—`).
