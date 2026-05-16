.PHONY: all build app run install clean test python backend tray frontend dmg icon
.DEFAULT_GOAL := app

APP_NAME     := ClaudeWatch
BUNDLE       := dist/$(APP_NAME).app
RESOURCES    := $(BUNDLE)/Contents/Resources
BIN_PATH     := .build/release/ClaudeWatchTray
BUILD_DIR    := build
PY_DIR       := $(BUILD_DIR)/python
REPO_ROOT    := $(realpath ..)
ICON_ICNS    := Resources/AppIcon.icns

# ───────────────────────────────── top-level targets ──────────────────────────

all: app

## tray — build only the Swift binary (no Python bundling). Fast iteration.
tray:
	swift build -c release

## python — download the portable Python interpreter (~50 MB on disk).
python:
	./scripts/download-python.sh

## backend — install claudewatch + deps into the bundled python.
backend: python
	./scripts/bundle-backend.sh

## icon — regenerate Resources/AppIcon.icns from the SwiftUI source.
## The .icns is committed to the repo; this target is only needed when the
## icon design changes.
icon:
	./scripts/make-icon.sh

## app — full pipeline: tray + python + backend + assemble .app bundle.
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
	@# Copy the bundled Python interpreter + site-packages
	rm -rf $(RESOURCES)/python
	cp -R $(PY_DIR) $(RESOURCES)/python
	@# Copy the dashboard frontend
	rm -rf $(RESOURCES)/frontend
	cp -R $(REPO_ROOT)/frontend $(RESOURCES)/frontend
	@# Strip pip-cached __pycache__ to shrink the bundle a bit
	find $(RESOURCES)/python -type d -name __pycache__ -prune -exec rm -rf {} +
	@echo ""
	@echo "✓ Built $(BUNDLE)"
	@du -sh $(BUNDLE) | awk '{print "  Bundle size: " $$1}'
	@du -sh $(RESOURCES)/python | awk '{print "  python/:     " $$1}'
	@du -sh $(RESOURCES)/frontend | awk '{print "  frontend/:   " $$1}'

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

## test — Swift package tests (separate from the Python pytest suite at repo root).
test:
	swift test

## clean — wipe build artifacts (keeps the cached Python tarball).
clean:
	swift package clean
	rm -rf dist
	rm -rf $(BUILD_DIR)/python
	@echo "  (Cached Python tarball preserved at $(BUILD_DIR)/.cache/)"

## distclean — also wipe the Python download cache.
distclean: clean
	rm -rf $(BUILD_DIR)
