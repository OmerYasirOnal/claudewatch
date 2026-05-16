import AppKit
import SwiftUI
import UserNotifications

/// UserDefaults key tracking whether the user has completed the first-launch
/// welcome flow. Toggling it back to `false` (via `WelcomeController.reset()`
/// or `defaults delete …`) re-opens the welcome on next launch / next call.
public let WelcomeShownDefaultsKey = "claudewatch.tray.welcomeShown"

/// Singleton owner of the welcome NSWindow. `MenuBarExtra` apps don't get a
/// natural place to host floating windows, so we manage the window ourselves
/// via NSHostingController + NSWindow and keep a strong reference here.
@MainActor
final class WelcomeController: NSObject, NSWindowDelegate {
    static let shared = WelcomeController()

    private var window: NSWindow?

    private override init() { super.init() }

    /// Open the welcome window. If it's already on screen, just brings it to
    /// the front. Calling this also activates the app so the window can show
    /// above other applications (LSUIElement apps aren't normally focused).
    func show() {
        if let win = window {
            NSApp.activate(ignoringOtherApps: true)
            win.makeKeyAndOrderFront(nil)
            return
        }

        let root = WelcomeView(onFinish: { [weak self] in self?.finish() })
        let hosting = NSHostingController(rootView: root)
        hosting.view.frame = NSRect(x: 0, y: 0, width: 560, height: 420)

        let win = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 560, height: 420),
            styleMask: [.titled, .closable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        win.title = "Welcome to ClaudeWatch"
        win.titlebarAppearsTransparent = true
        win.titleVisibility = .hidden
        win.isMovableByWindowBackground = true
        win.contentViewController = hosting
        win.center()
        win.isReleasedWhenClosed = false
        win.delegate = self
        win.level = .floating

        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
        win.makeKeyAndOrderFront(nil)

        window = win
    }

    /// Show the welcome window only if the user hasn't completed it yet.
    /// Idempotent — safe to call from app init on every launch.
    func showIfFirstLaunch() {
        let shown = UserDefaults.standard.bool(forKey: WelcomeShownDefaultsKey)
        if !shown { show() }
    }

    /// Mark welcome as completed and close the window.
    func finish() {
        UserDefaults.standard.set(true, forKey: WelcomeShownDefaultsKey)
        window?.close()
    }

    /// Reset state so the welcome appears again on the next `show()` call.
    func reset() {
        UserDefaults.standard.set(false, forKey: WelcomeShownDefaultsKey)
    }

    // MARK: NSWindowDelegate

    func windowWillClose(_ notification: Notification) {
        // Drop the reference so re-opening rebuilds a fresh view tree (the
        // SwiftUI state machine, including the panel index, resets).
        window = nil
        // Return to accessory mode so we stay a menu-bar app once the user
        // is done with the welcome window.
        if UserDefaults.standard.bool(forKey: WelcomeShownDefaultsKey) {
            NSApp.setActivationPolicy(.accessory)
        }
    }
}
