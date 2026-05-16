.PHONY: build run install clean test

APP_NAME    := ClaudeWatch
BUNDLE      := dist/$(APP_NAME).app
BIN_PATH    := .build/release/ClaudeWatchTray

build:
	swift build -c release
	@mkdir -p $(BUNDLE)/Contents/MacOS $(BUNDLE)/Contents/Resources
	cp $(BIN_PATH) $(BUNDLE)/Contents/MacOS/$(APP_NAME)
	cp Info.plist $(BUNDLE)/Contents/Info.plist
	@echo ""
	@echo "Built: $(BUNDLE)"
	@du -sh $(BUNDLE) | awk '{print "Size: " $$1}'

run: build
	@pkill -x "$(APP_NAME)" 2>/dev/null || true
	open $(BUNDLE)
	@echo "Launched. Look in the menu bar for the 🐜 icon."

install: build
	@mkdir -p ~/Applications
	@rm -rf ~/Applications/$(APP_NAME).app
	cp -R $(BUNDLE) ~/Applications/
	@echo "Installed to ~/Applications/$(APP_NAME).app"
	@echo "Open with: open ~/Applications/$(APP_NAME).app"

test:
	swift test

clean:
	swift package clean
	rm -rf dist
