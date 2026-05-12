# macOS Permissions Setup

ClaudeWatch only touches your own files and processes, but macOS still gates
two capabilities behind explicit permission grants.

## 1. iTerm2 Python API (required for iTerm tab detection + focus)

1. Open iTerm2
2. **Settings → General → Magic**
3. Check **Enable Python API**
4. Auth setting: **Confirm each time** (safer) or **Full Access** (less prompty)
5. Restart iTerm2

When ClaudeWatch first connects, iTerm2 will prompt to allow Python access —
click **Allow**.

**Symptom if missing:** dashboard shows the amber "iTerm2 Python API not
reachable" banner; all sessions appear as `headless`.

## 2. Automation permission (required for focus + new-session actions)

The first time you click **Focus** or **+ New session**, macOS will prompt:

> "Python" wants to control "iTerm2.app"

Click **OK**. To check or change later:

- **System Settings → Privacy & Security → Automation**
- Under Python (or whichever process is running `claudewatch`), the toggle for
  **iTerm** should be on.

**Symptom if missing:** focus/new-session return HTTP 500 with
"`Not authorized to send Apple events to iTerm`" in the error body.

## 3. Conversation log access

Reading `~/.claude/projects/` requires no permission — the files belong to your
user. If `claudewatch` reports "log_dir_found: false", you probably haven't run
Claude Code on this machine yet.

## Notes

- ClaudeWatch never prompts for accessibility or screen recording.
- It binds to `127.0.0.1:7788` only. No network listener is exposed.
- The Halt action sends SIGINT and never escalates to SIGKILL.
