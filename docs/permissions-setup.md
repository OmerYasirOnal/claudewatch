# macOS Permissions Setup

ClaudeWatch only touches your own files and processes, but macOS still gates
two capabilities behind explicit permission grants: **Automation** (so we
can talk to iTerm) and **Notifications** (so the daemon can post the
session-end banner).

How those grants are obtained depends on which install path you took.

## Path A — Tray `.app` (recommended)

The native menu bar app's first-launch flow walks you through the prompts:

1. Launch `/Applications/ClaudeWatch.app` for the first time.
2. The welcome window asks for **Automation** permission (for iTerm) and
   **Notifications** permission. Click **Allow** on each macOS prompt.
3. The menu bar icon appears; you're done.

To re-run the welcome flow later (after revoking a permission, or to confirm
everything's wired up), click **Show welcome again** at the bottom of the
popover.

## Path B — Daemon via `pip install` (development / CLI)

There is no automatic prompt — the daemon will request capabilities the
first time it needs them, and macOS may silently deny the grant until you
toggle it manually.

1. **Automation** — System Settings → **Privacy & Security → Automation**.
   Under whichever app is hosting `claudewatch` (Terminal, iTerm, or
   `osascript`), enable the toggle for **iTerm**. If you haven't run the
   focus or new-session action yet, the entry won't exist; trigger one
   first to make it appear.
2. **Notifications** — System Settings → **Notifications**, find
   **ClaudeWatch** (or the parent Terminal/iTerm hosting it), and allow
   banners / sounds.

## What each permission is used for

### iTerm2 Python API (required for iTerm tab detection + Focus action)

1. Open iTerm2.
2. **Settings → General → Magic**.
3. Check **Enable Python API**.
4. Auth setting: **Confirm each time** (safer) or **Full Access** (less
   prompty).
5. Restart iTerm2.

When ClaudeWatch first connects, iTerm2 will prompt you to allow Python
access — click **Allow**.

**Symptom if missing:** the dashboard shows the amber "iTerm2 Python API
not reachable" banner; every session appears as `headless`.

### Automation (required for focus + new-session + osascript notifications)

The first time you click **Focus** or **+ New session**, macOS prompts:

> "Python" wants to control "iTerm2.app"

Click **OK**. To check or change later:

- **System Settings → Privacy & Security → Automation**.
- Under Python (or whichever process is running `claudewatch`), the toggle
  for **iTerm** should be on.

**Symptom if missing:** focus / new-session return HTTP 500 with
"`Not authorized to send Apple events to iTerm`" in the body.

### Notifications (optional but recommended)

Session-end notifications, high-cost warnings, etc. are sent via
`osascript display notification`, which surfaces in the user's standard
notification center.

- **System Settings → Notifications** → ClaudeWatch (or the parent
  Terminal / iTerm host) → Allow notifications.
- If you launched via `claudewatch start` from your shell, the
  notifications attribute to that shell's parent app, not to "ClaudeWatch"
  — adjust accordingly.

### Conversation log access

Reading `~/.claude/projects/` requires no permission — the files belong to
your user. If `claudewatch` reports `log_dir_found: false`, you probably
haven't run Claude Code on this machine yet.

## Notes

- ClaudeWatch never asks for Accessibility or Screen Recording.
- It binds to `127.0.0.1:7788` only. No network listener is exposed.
- The Halt action sends SIGINT and never escalates to SIGKILL.
