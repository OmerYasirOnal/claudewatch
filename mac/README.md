# ClaudeWatch Tray — native macOS menu bar companion

Lightweight SwiftUI app that lives in your menu bar and shows live status
from the local `claudewatch` daemon. **Backend stays Python** — the tray is
a thin native UI layer that polls `http://127.0.0.1:7788`.

## Why

- Always-visible session count + cost in your menu bar
- One-click Focus / Halt for any session (no browser tab required)
- Quick access to the full dashboard via "Open Dashboard"
- Works alongside the web dashboard; either or both can be running

## Requirements

- macOS 13+ (Ventura). Built and tested on macOS 26 (Tahoe).
- Swift 6 (Xcode 16+ command-line tools)
- The `claudewatch` Python daemon running locally:

```bash
claudewatch install-daemon   # auto-start at login, recommended
# or
claudewatch start --daemon   # one-off
```

## Build & install

```bash
cd mac
make install              # builds .app and copies to ~/Applications
open ~/Applications/ClaudeWatch.app
```

The icon `🐜` (placeholder) appears in your menu bar with the active session
count next to it. Click for the popover.

## Develop

```bash
make run                  # build + launch
# or
swift run ClaudeWatchTray
```

## Auto-start at login

After `make install`:
1. System Settings → General → Login Items
2. Add `~/Applications/ClaudeWatch.app`

(Or use Path B follow-up: a tiny `make install-login-item` target. Not yet wired.)

## Architecture

```
ClaudeWatchTrayApp.swift   @main, MenuBarExtra label
AppViewModel.swift          @MainActor ObservableObject, 3-second poll loop
APIClient.swift             actor; URLSession wrapper around the local API
Models.swift                Codable structs matching backend ClaudeSession JSON
Views/
  MenuBarContent.swift      Popover root (header + scrollable list + footer)
  SessionRow.swift          Per-session card with status dot + Focus/Halt buttons
```

Endpoints consumed (all `127.0.0.1:7788`):
- `GET /api/sessions`
- `GET /api/health`
- `POST /api/sessions/{pid}/focus`
- `POST /api/sessions/{pid}/halt`

The backend's `TrustedHostMiddleware` (added in #39) explicitly allows
`127.0.0.1` and `localhost`, so the tray's requests are accepted.

## Roadmap (Path B V1.1+)

- **Actionable notifications** via `UNUserNotificationCenter` — Focus/Halt
  buttons attached to "session ended" / "high cost" alerts
- **SSE consumption** (`URLSession.bytes(for:)`) instead of polling
- **Embedded chat** via the `/api/sessions/{pid}/log-stream` + `/send-text`
  endpoints from PR #68 — opens a side window
- **Per-session quick-launch templates**
- **Optional code-signing + notarization** for App Store / direct distribution

## Privacy & safety

- Tray only talks to `127.0.0.1`. No outbound network.
- `Info.plist` declares `NSAllowsLocalNetworking` exactly for that.
- The Focus/Halt actions hit the same authenticated-by-loopback endpoints the
  web dashboard uses.
- Remote-text-send is **opt-in** in the web dashboard's Settings; the tray
  doesn't expose it in V1.
