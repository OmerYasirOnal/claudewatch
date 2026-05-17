# ClaudeWatch — native macOS app

![mac CI](https://github.com/OmerYasirOnal/claudewatch/actions/workflows/mac-ci.yml/badge.svg)

A standalone .app for monitoring your local Claude Code sessions. Drag to
`/Applications`, double-click, done — no Python install required.

| | |
|---|---|
| **macOS** | 14 (Sonoma) or newer, Apple Silicon (or Intel via `UNIVERSAL=1` build) |
| **Bundle size** | ~81 MB single-arch / ~157 MB universal (portable Python 3.12 + backend) |
| **First-run cost** | ~50 MB on initial Python download (~100 MB universal, both cached) |
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
- `make app-universal` — same as `UNIVERSAL=1 make app` (see below)
- `make run` — build + launch
- `make install` — copy to `~/Applications/ClaudeWatch.app`
- `make dmg` — wrap in `dist/ClaudeWatch.dmg` for distribution
- `make clean` / `make distclean`

## Universal builds (Apple Silicon + Intel)

Setting `UNIVERSAL=1` builds a `.app` that runs natively on both Apple
Silicon (arm64) and Intel (x86_64) Macs. Under the hood we download both
arch flavors of [python-build-standalone](https://github.com/astral-sh/python-build-standalone),
extract them side-by-side as `Contents/Resources/python-arm64/` and
`Contents/Resources/python-x86_64/`, and let `PythonRunner.swift` pick the
right interpreter at runtime via `#if arch(...)`. The Swift menu bar
binary is already universal (compiled with both slices when Swift builds
on macOS 14+); only the bundled Python needs the dual tree. We deliberately
do NOT `lipo`-merge the full python-build-standalone distribution: it ships
hundreds of `.so` extension modules and the merge is fragile across PBS
releases.

```bash
make UNIVERSAL=1 app && make dmg
# or, equivalently:
make app-universal && make dmg
```

**Size impact (measured)**: the bundled Python (interpreter + backend
site-packages) is the dominant cost.

| Build | `.app` on disk | DMG (UDZO) |
|-------|---------------:|-----------:|
| Single-arch | ~81 MB | ~29 MB |
| Universal (`UNIVERSAL=1`) | ~157 MB | ~57 MB |

For a release that wants Intel coverage the ~28 MB DMG delta is acceptable;
for personal dev builds, omit the flag.

**Runtime arch detection.** `PythonRunner.hostArchSuffix` uses Swift's
`#if arch(arm64) / arch(x86_64)` compile-time check to pick a suffix at
build time per slice of the universal Swift binary. At launch,
`locatePython()` walks `Contents/Resources/<container>` looking for, in
order: `python-<arch>/bin/python3` (universal), `python/bin/python3`
(single-arch / merged). The first executable wins. The Swift binary
itself is universal in both builds — only the Python tree differs.

**On Rosetta-only systems** (Intel hardware): the `x86_64` slice of the
Swift binary runs natively, `hostArchSuffix` returns `"x86_64"`, and the
runner picks the `python-x86_64/` tree. On a universal build everything
just works. On a single-arch (arm64-only) build the runner falls back to
`python/bin/python3` which is an arm64 interpreter — `posix_spawn` returns
`ENOEXEC` and the popover shows "Bundled Python not found". See the
[troubleshooting entry](../docs/troubleshooting.md#intel-mac-sees-bundled-python-not-found).

**On Apple Silicon running an Intel-only app under Rosetta 2**: doesn't
apply here — the Swift binary is universal, so macOS always runs the
arm64 slice, which then loads the arm64 Python.

For tagged releases, set `UNIVERSAL=1` in `.github/workflows/release.yml` so
the published DMG runs everywhere. The default `make app` invocation in CI is
otherwise unchanged.

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

## Chat panel

Per-session **Chat** opens a native window that streams the conversation
log over SSE and lets you reply via `/api/sessions/{pid}/send-text`.

- **Cmd+Return** sends. Plain `Return` inserts a newline (matches iMessage
  / Cursor / the web composer). The shortcut is wired to a SwiftUI
  `Button` with `.keyboardShortcut(.return, modifiers: [.command])` so it
  fires even while the composer `TextEditor` has focus.
- **Markdown rendering** — assistant turns are rendered via SwiftUI's
  `Text(.init(string))` `LocalizedStringKey` markdown path: inline
  formatting (bold, italics, `` `code` ``, links). User-typed,
  tool-use, tool-result, and system entries stay plain text — a stray
  `*` or `_` in a user message would silently disappear under markdown
  parsing.
- **Auto-scroll** — a `Color.clear` sentinel pinned at the bottom of the
  transcript is `scrollTo("BOTTOM", anchor: .bottom)`'d via `withAnimation`
  on every `vm.entries.count` change, so new turns slide into view.
- **Focus-on-open** — the composer takes focus one runloop tick after the
  window opens (`@FocusState` + `DispatchQueue.main.async`) so the user
  can start typing immediately without clicking in.
- Send requires `config.remote_control.enabled = true` (Settings → Remote
  Control). The 403 from the backend surfaces as an inline error in the
  composer.

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
├── MacOS/ClaudeWatch           Swift binary (~400 KB, universal)
└── Resources/
    ├── python/                 portable cpython-3.12.7 (single-arch build)
    │   ├── bin/python3                  ← or python-arm64/ + python-x86_64/
    │   └── lib/python3.12/site-packages/    in a UNIVERSAL=1 build
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

## Test

The Swift target ships with an XCTest suite covering JSON decoding for
`Session` / `HealthReport`, the `APIClient` happy + error paths (via a
`URLProtocol` mock — no real HTTP), the lenient `AppConfig` decoder, and
smoke tests for `PythonRunner`'s port-busy / no-bundle branches.

```bash
cd mac
make test            # or: swift test
```

The same suite runs on every push / PR that touches `mac/`, `backend/`, or
`frontend/` via [`.github/workflows/mac-ci.yml`](../.github/workflows/mac-ci.yml).
CI does **not** download python-build-standalone or assemble the `.app` — those
need network and don't catch regressions worth the runtime cost.

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
- [x] Embedded chat panel using `/api/sessions/{pid}/log-stream` + `/send-text`
- [ ] Sparkle for in-app auto-updates
- [x] Universal binary (x86_64 + arm64) — `make app-universal` / `UNIVERSAL=1`
- [ ] Code signing + notarization in CI
