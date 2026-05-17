.PHONY: all build app app-universal run install clean test python backend tray frontend dmg icon sign sign-local sign-notarize release-signed
.DEFAULT_GOAL := app

APP_NAME     := ClaudeWatch
BUNDLE       := dist/$(APP_NAME).app
RESOURCES    := $(BUNDLE)/Contents/Resources
BIN_PATH     := .build/release/ClaudeWatchTray
BUILD_DIR    := build
PY_DIR       := $(BUILD_DIR)/python
PY_DIR_ARM64 := $(BUILD_DIR)/python-arm64
PY_DIR_X8664 := $(BUILD_DIR)/python-x86_64
REPO_ROOT    := $(realpath ..)
ICON_ICNS    := Resources/AppIcon.icns

# UNIVERSAL=1 → bundle both arm64 + x86_64 python trees so the .app runs on
# Apple Silicon and Intel Macs. Default unset preserves the historical single-
# arch build. See mac/README.md → "Universal builds".
UNIVERSAL ?= 0

# ───────────────────────────────── top-level targets ──────────────────────────

all: app

## tray — build only the Swift binary (no Python bundling). Fast iteration.
tray:
	swift build -c release

## python — download the portable Python interpreter (~50 MB on disk).
## With UNIVERSAL=1, downloads BOTH arm64 + x86_64 trees (~100 MB total).
python:
	@if [ "$(UNIVERSAL)" = "1" ]; then \
		UNIVERSAL=1 ./scripts/download-python.sh; \
	else \
		./scripts/download-python.sh; \
	fi

## backend — install claudewatch + deps into the bundled python.
## Auto-detects UNIVERSAL trees from `make python UNIVERSAL=1` and installs
## into both; otherwise falls back to single-tree install.
backend: python
	./scripts/bundle-backend.sh

## icon — regenerate Resources/AppIcon.icns from the SwiftUI source.
## The .icns is committed to the repo; this target is only needed when the
## icon design changes.
icon:
	./scripts/make-icon.sh

## app — full pipeline: tray + python + backend + assemble .app bundle.
## With UNIVERSAL=1, ships both arm64 + x86_64 python trees (~95 MB DMG vs ~50 MB).
app: tray python backend
	@echo "→ Assembling $(BUNDLE)"
	@mkdir -p $(BUNDLE)/Contents/MacOS $(RESOURCES)
	cp $(BIN_PATH) $(BUNDLE)/Contents/MacOS/$(APP_NAME)
	cp Info.plist $(BUNDLE)/Contents/Info.plist
	@# Copy the pre-built AppIcon.icns (regenerate with `make icon`).
	@if [ -f $(ICON_ICNS) ]; then \
		cp $(ICON_ICNS) $(RESOURCES)/AppIcon.icns; \
	else \
		echo "  ⚠ $(ICON_ICNS) missing — run 'make icon' to generate"; \
	fi
	@# Copy the bundled Python interpreter(s) + site-packages.
	@# Universal: copy BOTH arch trees and skip the legacy `python/` (the
	@# dev-mode symlink would be useless inside the bundle anyway).
	@# Single-arch: copy the legacy `python/` tree.
	rm -rf $(RESOURCES)/python $(RESOURCES)/python-arm64 $(RESOURCES)/python-x86_64
	@if [ -d $(PY_DIR_ARM64) ] && [ -d $(PY_DIR_X8664) ] && \
	    [ ! -L $(PY_DIR_ARM64) ] && [ ! -L $(PY_DIR_X8664) ]; then \
		echo "  → universal: copying arm64 + x86_64 python trees"; \
		cp -R $(PY_DIR_ARM64) $(RESOURCES)/python-arm64; \
		cp -R $(PY_DIR_X8664) $(RESOURCES)/python-x86_64; \
	else \
		echo "  → single-arch: copying python tree"; \
		cp -RH $(PY_DIR) $(RESOURCES)/python; \
	fi
	@# Copy the dashboard frontend
	rm -rf $(RESOURCES)/frontend
	cp -R $(REPO_ROOT)/frontend $(RESOURCES)/frontend
	@# Strip pip-cached __pycache__ to shrink the bundle a bit
	@for d in $(RESOURCES)/python $(RESOURCES)/python-arm64 $(RESOURCES)/python-x86_64; do \
		[ -d $$d ] && find $$d -type d -name __pycache__ -prune -exec rm -rf {} + || true; \
	done
	@echo ""
	@echo "✓ Built $(BUNDLE)"
	@du -sh $(BUNDLE) | awk '{print "  Bundle size: " $$1}'
	@[ -d $(RESOURCES)/python ] && du -sh $(RESOURCES)/python | awk '{print "  python/:        " $$1}' || true
	@[ -d $(RESOURCES)/python-arm64 ] && du -sh $(RESOURCES)/python-arm64 | awk '{print "  python-arm64/:  " $$1}' || true
	@[ -d $(RESOURCES)/python-x86_64 ] && du -sh $(RESOURCES)/python-x86_64 | awk '{print "  python-x86_64/: " $$1}' || true
	@du -sh $(RESOURCES)/frontend | awk '{print "  frontend/:   " $$1}'

## app-universal — convenience shortcut for `UNIVERSAL=1 make app`.
## Produces a .app that runs natively on both Apple Silicon and Intel Macs.
app-universal:
	$(MAKE) UNIVERSAL=1 app

## run — build and launch the .app (kills any prior instance first).
run: app
	@pkill -x $(APP_NAME) 2>/dev/null || true
	@sleep 1
	open $(BUNDLE)
	@echo "✓ Launched. Look for the 🐜 icon in your menu bar."

## install — copy the bundle into ~/Applications/ for daily use.
install: app
	@mkdir -p ~/Applications
	@rm -rf ~/Applications/$(APP_NAME).app
	cp -R $(BUNDLE) ~/Applications/
	@echo "✓ Installed to ~/Applications/$(APP_NAME).app"
	@echo "  Launch:  open ~/Applications/$(APP_NAME).app"

## dmg — package the .app as a drag-to-Applications DMG for distribution.
dmg: app
	@mkdir -p dist
	@rm -f dist/$(APP_NAME).dmg
	@./scripts/make-dmg.sh
	@echo "✓ Built dist/$(APP_NAME).dmg"
	@du -sh dist/$(APP_NAME).dmg | awk '{print "  DMG size: " $$1}'

## sign — code-sign + notarize + staple. Requires Developer ID cert.
## See mac/docs/code-signing.md for one-time setup. Run after `make dmg`.
sign: dmg
	@./scripts/sign-and-notarize.sh

## sign-local — sign without uploading to Apple notary service (testing only).
sign-local: dmg
	SKIP_NOTARIZE=1 ./scripts/sign-and-notarize.sh

## sign-notarize — explicit signing pipeline target used by CI. Builds the .app
## and DMG first, then drives sign-and-notarize.sh with the canonical paths.
## Identical to `sign` today but kept as a named target so the workflow file
## and the Makefile can evolve independently without breaking callers.
sign-notarize: app dmg
	./scripts/sign-and-notarize.sh dist/$(APP_NAME).app dist/$(APP_NAME).dmg

## release-signed — full from-clean release pipeline. Wipes prior artifacts,
## rebuilds the .app + DMG, then signs + notarizes + staples. This is what
## you run on a release machine when you want a single command to produce a
## ready-to-ship DMG.
release-signed: clean app dmg sign-notarize
	@echo "✓ Release-ready signed + notarized DMG: dist/$(APP_NAME).dmg"

## test — Swift package tests (separate from the Python pytest suite at repo root).
test:
	swift test

## clean — wipe build artifacts (keeps the cached Python tarball).
clean:
	swift package clean
	rm -rf dist
	rm -rf $(BUILD_DIR)/python $(PY_DIR_ARM64) $(PY_DIR_X8664)
	@echo "  (Cached Python tarball(s) preserved at $(BUILD_DIR)/.cache/)"

## distclean — also wipe the Python download cache.
distclean: clean
	rm -rf $(BUILD_DIR)
