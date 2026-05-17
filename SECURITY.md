# Security Policy

ClaudeWatch runs entirely on `localhost`. It binds to `127.0.0.1:7788`, talks
only to local processes (`ps`, AppleScript, tmux) and reads local files
(`~/.claude/projects/**`, the session's git worktree). It never makes outbound
network requests and ships no telemetry.

## Threat model

Because the surface is local-only, the bugs that matter to us are the ones
that let a malicious *local* actor (a website in the user's browser, a
crafted conversation log, a hostile cwd path) escape ClaudeWatch's intended
boundaries. The classes we actively defend against:

- **DNS rebinding** — the API enforces `Host` header allow-listing so a
  remote page can't reach `127.0.0.1:7788` via a rebound DNS name.
- **Path traversal** — every path the API accepts is resolved and checked to
  be inside an expected root (the session cwd, the user's
  `~/.claude/projects`, etc.) before any read.
- **Command injection** — AppleScript and shell invocations take arguments
  as arrays; we never string-interpolate user-controlled values into a
  command line.
- **Unsafe `new-session` arguments** — flags are passed through a strict
  whitelist; cwd is sandboxed under the user's home.
- **Symlink escape** — we resolve symlinks before bounds-checking.

Past fixes in this area:
[#38](https://github.com/OmerYasirOnal/claudewatch/issues/38),
[#39](https://github.com/OmerYasirOnal/claudewatch/issues/39),
[#41](https://github.com/OmerYasirOnal/claudewatch/issues/41),
[#42](https://github.com/OmerYasirOnal/claudewatch/issues/42),
[#44](https://github.com/OmerYasirOnal/claudewatch/issues/44),
[#88](https://github.com/OmerYasirOnal/claudewatch/issues/88).

Out of scope:

- An attacker who already has interactive shell access to the user account
  (they don't need ClaudeWatch to do anything they can't already do).
- Resource exhaustion via genuinely enormous local conversation logs.
- macOS Gatekeeper / notarization — the V1 build is unsigned; that's a
  known posture, not a vulnerability (see `mac/docs/code-signing.md`).

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security reports.

Two channels, either is fine:

1. **GitHub Private Vulnerability Reporting** — go to
   [Security → Report a vulnerability](https://github.com/OmerYasirOnal/claudewatch/security/advisories/new).
   This is preferred because it keeps the triage thread attached to the
   repo.
2. **Email** — `omeryasir.onal@stu.fsm.edu.tr`. Use a subject line that
   includes "claudewatch security" so it doesn't get lost.

Include, if you can:

- A description of the issue and which class above it falls under (or a new
  class).
- Reproduction steps or a proof of concept. Local scripts are fine; please
  don't share anything that targets a third party.
- The version of ClaudeWatch (`claudewatch --version`) and macOS.
- Whether you've coordinated with anyone else on disclosure.

## Response SLA

This is a single-maintainer project, so response is best-effort:

- **Acknowledgement** — within 7 days.
- **Triage + initial assessment** — within 14 days of acknowledgement.
- **Fix + advisory** — timing depends on severity; critical issues are
  prioritised. We'll keep you in the loop on the report thread.

Once a fix lands, we publish a GitHub Security Advisory with credit to the
reporter (unless you'd rather stay anonymous — just say so).

Thanks for helping keep ClaudeWatch safe.
