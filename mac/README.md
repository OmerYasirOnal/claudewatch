# ClaudeWatch — native macOS app

A standalone .app for monitoring your local Claude Code sessions. Drag to
`/Applications`, double-click, done — no Python install required.

| | |
|---|---|
| **macOS** | 14 (Sonoma) or newer, Apple Silicon |
| **Bundle size** | ~78 MB (includes a portable Python 3.12 + the backend) |
| **First-run cost** | ~50 MB on initial Python download (cached for rebuilds) |
| **Resources** | Lives entirely in `~/.claudewatch/` (config, state, logs) |

## Architecture

ClaudeWatch is two halves wrapped in one bundle:

1. **Native Swift menu bar app** (`ClaudeWatchTray`)
   - Lives in the macOS menu bar (no Dock icon — `LSUIElement=true`)
   - Shows active session count + running cost
   - Popover with per-session Focus/Halt/Chat buttons
   - Settings window for all backend config
   - Spawns and supervises the bundled Python on launch

2. **Bundled Python backend** (FastAPI + scheduler + detectors)
   - Lives in `Contents/Resources/python/lib/python3.12/site-packages/`
   - Started on first popover open via `PythonRunner.swift`
   - Talks to iTerm via the iTerm2 Python API
   - Serves the existing HTML dashboard at `http://127.0.0.1:7788/`

The Swift app gracefully detects an existing `claudewatch` daemon on port 7788
(e.g. one launched via `claudewatch install-daemon`) and won't double-spawn —
it just observes that instance instead.

## Build

```bash
cd mac
make app                  # tray + portable Python + backend + assemble .app
```

Targets:
- `make tray` — only the Swift binary (fast iteration)
- `make python` — download python-build-standalone (cached on disk)
- `make backend` — pip-install the claudewatch backend into the bundled python
- `make app` — full pipeline → `dist/ClaudeWatch.app`
- `make run` — build + launch
- `make install` — copy to `~/Applications/ClaudeWatch.app`
- `make dmg` — wrap in `dist/ClaudeWatch.dmg` for distribution
- `make clean` / `make distclean`

## Install (end user)

1. Download `ClaudeWatch.dmg` from
   [Releases](https://github.com/OmerYasirOnal/claudewatch/releases)
2. Double-click to mount → drag `ClaudeWatch.app` into `Applications`
3. First launch: a **welcome window** walks you through Automation (iTerm
   AppleScript control) and Notifications permission grants, then drops you
   back to the menu bar with the dashboard one click away.
4. The 🐜 icon appears in your menu bar. Click it for the popover.

> To re-run the welcome flow at any time, click **Show welcome again** at the
> bottom of the popover. To force a fresh first-launch (e.g. for testing):
> `defaults delete com.omeryasironal.claudewatch.tray claudewatch.tray.welcomeShown`.

## App icon

`mac/Resources/AppIcon.icns` is committed to the repo so end users get the
emerald→cyan ant icon out of the box. To regenerate (after tweaking colors or
swapping the glyph):

```bash
make icon          # → mac/Resources/AppIcon.icns
```

That target runs `scripts/render-icon.swift` (a pure AppKit renderer) to
produce a 1024×1024 source PNG, then `scripts/make-icon.sh` resizes via
`sips` into a complete `iconset` and runs `iconutil` to package the `.icns`.
The `app` target picks up `Resources/AppIcon.icns` automatically.

### Auto-start at login

1. System Settings → General → Login Items
2. Add `ClaudeWatch.app`

(A future `claudewatch install-loginitem` command will automate this.)

## Settings (native)

Cmd+, from the popover (or click the gear) opens the native Settings window:

- **General** — Anthropic plan (`api` / `pro` / `max` / `max_20x` / `team` / `free`),
  scheduler intervals, privacy mode, read-only mode
- **Notifications** — master toggle, per-event triggers, cost threshold
- **Editor** — `Open in editor` command (default `code`)
- **Remote Control** — toggle for the dashboard's chat-send capability
- **About** — version, links

All settings persist in `~/.claudewatch/config.toml` and survive app
reinstalls.

## Layout (inside the .app bundle)

```
ClaudeWatch.app/Contents/
├── Info.plist                  LSUIElement=true → menu bar only
├── MacOS/ClaudeWatch           Swift binary (~400 KB)
└── Resources/
    ├── python/                 portable cpython-3.12.7-aarch64-darwin
    │   ├── bin/python3
    │   └── lib/python3.12/site-packages/
    │       ├── backend/        the claudewatch Python package
    │       ├── fastapi/  uvicorn/  iterm2/  ...
    │       └── ...
    └── frontend/               HTML+JS dashboard (~92 KB)
```

`Contents/Resources/python/bin/python3 -m uvicorn backend.server:app` is what
the Swift `PythonRunner` invokes on startup. `CLAUDEWATCH_FRONTEND_DIR` env is
set so the backend serves the dashboard from the bundle.

## Develop

```bash
# Fast Swift-only iteration (the bundled Python lives in build/python from a
# prior `make python && make backend`)
swift run ClaudeWatchTray

# Or build the full .app:
make run
```

If you only need to iterate on the backend, `claudewatch start --daemon` still
works in parallel. The Swift app will detect it and go into `.external` state.

## Distribution checklist

For a real release (not just dev builds):

1. **Apple Developer ID** (\$99/year, not yet configured)
2. Code sign: `codesign --deep --force --options runtime --sign "Developer ID Application: ..." dist/ClaudeWatch.app`
3. Notarize: `xcrun notarytool submit dist/ClaudeWatch.dmg --keychain-profile "..." --wait`
4. Staple: `xcrun stapler staple dist/ClaudeWatch.dmg`
5. Upload to GitHub Releases

Without code signing, users see a Gatekeeper warning on first launch and have
to right-click → Open → confirm. This is fine for a dev preview but blocks
casual users.

## Roadmap

- [ ] Actionable notifications via `UNUserNotificationCenter` (Focus/Halt buttons)
- [ ] SSE consumption (`URLSession.bytes(for:)`) replacing the 3 s poll
- [ ] Embedded chat panel using `/api/sessions/{pid}/log-stream` + `/send-text`
- [ ] Sparkle for in-app auto-updates
- [ ] Universal binary (x86_64 + arm64)
- [ ] Code signing + notarization in CI
