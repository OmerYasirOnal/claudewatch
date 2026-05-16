# Troubleshooting

Real failure modes observed during normal use. Each entry has a one- or
two-sentence cause and a fix.

## Install / startup

### `claudewatch start --daemon` exits but no server appears

- Stale PID at `~/.claudewatch/server.pid` from a previous unclean exit.
- Check `~/.claudewatch/logs/server.log` first; if empty / nothing fresh,
  delete the PID file and retry.

### Tests crash on macOS Python 3.9

- ClaudeWatch is `requires-python = ">= 3.10"`. macOS still ships 3.9 as
  the legacy system Python.
- Use Homebrew's `/opt/homebrew/bin/python3` or install 3.10+ via `pyenv`,
  then re-create the venv.

### DMG won't open: "ClaudeWatch.app is damaged and can't be opened"

- The .app is currently unsigned (no Developer ID — see the
  [mac/README distribution checklist](../mac/README.md#distribution-checklist)).
  macOS Gatekeeper quarantines downloads from unsigned distributors.
- Either right-click the .app → **Open** → confirm, or strip the
  quarantine attribute:
  ```bash
  xattr -dr com.apple.quarantine /Applications/ClaudeWatch.app
  ```

### Tray menu bar icon shows but popover is empty

- The Python backend failed to launch. The popover renders with no
  sessions because there's no `/api/sessions` answering.
- Click **Show backend status** in the popover footer to see stderr from
  the bundled Python. Most common cause is a missing macOS Automation
  permission for iTerm (see permissions-setup.md).

## Sessions

### All sessions show as `headless`

- iTerm2 Python API is disabled or not granted. Sessions are still
  detected — you just lose the tab/window linkage and the Focus action.
- See [permissions-setup.md](permissions-setup.md). Enable Python API in
  iTerm Settings → General → Magic and restart iTerm.

### Focus button does nothing

- The session has no `iterm_session_id` populated in `/api/sessions`
  (which is what the persistent Python-API focus path matches on).
- Check `claudewatch info <pid>`. If `iterm_session_id` is null, the
  iTerm Python API isn't returning sessions; the tray will fall back to
  the AppleScript path (focus-by-tty), which requires Automation
  permission for `osascript`.

### "iTerm windows keep grabbing focus"

- Pre-#21 behavior: ClaudeWatch was opening a fresh iTerm Python API
  connection every 2 seconds, and on macOS Sonoma+ each connect briefly
  re-activated iTerm in the window order.
- Make sure the daemon is on a current build (post-commit `858f8f9`); the
  `ItermConnectionManager` now holds a single long-lived WebSocket and the
  iTerm refresh runs on its own 5s loop. See #2.

### Two sessions share the same conversation stats

- The cmdline didn't include `--resume <sessionId>` or `--session-id`, so
  the parser falls back to "freshest log in this cwd". Two `claude`
  processes in the same cwd match the same file.
- The current Claude CLI almost always sets one of those flags; headless
  agent SDK scripts sometimes don't. If you control the launcher, pass
  `--session-id`.

### Token usage / cost shows "—"

- The session's conversation log wasn't found. Common causes:
  - `~/.claude/projects/` doesn't exist (you haven't run Claude Code on
    this Mac yet).
  - The session was started outside the user's home directory tree.
  - A custom log directory is configured in Claude Code that we don't
    probe.
- Verify with `claudewatch info <pid>` — if `conversation_log_path` is
  null, that's the gap.

### Halt sends SIGINT but Claude keeps running

- ClaudeWatch never escalates beyond SIGINT (by design).
- If the session is stuck in a non-interruptible call, terminate the host
  tab/process yourself.

## Shutdown

### Daemon stop hangs (pre-#27 behavior)

- Old behavior: an SSE generator (open browser tab) blocked on the queue
  with no way to wake up; lifespan shutdown waited up to 15 seconds for
  each one before cancelling.
- Post-#27 the lifespan sets a `shutdown_event` and SSE generators race
  the queue against that event, exiting immediately. If you still see a
  hang, close any open dashboard tabs or update to a build with the
  `shutdown_event` plumbing.

## Settings / cost

### Cost is showing for my Max plan

- The dashboard hides $ figures only when the configured `plan` is
  non-metered. Default is `api`.
- Open Settings → Plan → set to **Max** (or `max_20x`, `team`, `pro`,
  `free`). See post-#71 plan-aware UI.

### Send to session: "Remote control is disabled"

- `/api/sessions/{pid}/send-text` is opt-in.
- Settings → Remote Control → enable. Post-#68/71.

### Pricing shows wrong numbers

- The defaults in `~/.claudewatch/config.toml` `[pricing]` are
  placeholders.
- Edit and verify against [anthropic.com/pricing](https://anthropic.com/pricing).
  Unknown models get `cost = null` (rendered as `—`).

## Files panel

### Files tab is empty

- `FilesystemWatcher` hasn't observed any file changes recently. The
  retention window is `file_change_retention_minutes` (default 10);
  values older than that are pruned from the per-cwd deque.
- Either change a file in an active session's cwd, or extend the retention
  in Settings.

### Files tab missing things you'd expect to see

- The per-cwd watcher honours `ignore_patterns` in config (default ignores
  `.git/`, `node_modules/`, `__pycache__/`, `dist/`, `build/`, `.next/`,
  …). Bursty changes inside those are silently dropped.
- Extend `ignore_patterns` if you have noisy directories that aren't on
  the default list; trim it to surface ones that are.

## Notifications

### Notifications not appearing

- macOS gate. Open **System Settings → Notifications**, find
  **ClaudeWatch**, and allow notifications (banner / sound as you prefer).
- Notifications are issued via `osascript`, so the daemon path also needs
  Automation permission for `osascript` to be granted. See
  [permissions-setup.md](permissions-setup.md).

### Focus action fails with "Not authorized to send Apple events"

- Automation permission missing for the process hosting `claudewatch`
  (Terminal, iTerm, Python, or the .app itself).
- **System Settings → Privacy & Security → Automation** → toggle on for
  iTerm under the relevant parent.

## Performance

### CPU usage of ClaudeWatch itself feels high

- Default `process_scan_interval_seconds = 2`. Bump it in
  `~/.claudewatch/config.toml`:
  ```toml
  process_scan_interval_seconds = 5
  ```
- The filesystem watcher's activity also rises with many active cwds.
  Large repos that re-build often (`node_modules`, build dirs) are
  already filtered by default; extend `ignore_patterns` if needed.
