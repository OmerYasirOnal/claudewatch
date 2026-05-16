import AppKit
import SwiftUI

/// Singleton manager for chat windows. Keeps at most one open NSWindow per
/// PID — clicking "Chat" on a session row that's already open just brings
/// the existing window forward.
@MainActor
final class ChatWindowController: NSObject, NSWindowDelegate {
    static let shared = ChatWindowController()

    private struct Entry {
        let window: NSWindow
        let viewModel: ChatViewModel
    }

    private var windowsByPid: [Int: Entry] = [:]

    private override init() { super.init() }

    /// Open (or focus, if already open) a chat window for the given session.
    func openChat(for session: Session) {
        if let existing = windowsByPid[session.pid] {
            NSApp.activate(ignoringOtherApps: true)
            existing.window.makeKeyAndOrderFront(nil)
            return
        }

        let vm = ChatViewModel(session: session)
        let root = ChatView(vm: vm, onClose: { [weak self] in
            self?.closeChat(pid: session.pid)
        })
        let hosting = NSHostingController(rootView: root)
        hosting.view.frame = NSRect(x: 0, y: 0, width: 540, height: 540)

        let win = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 540, height: 540),
            styleMask: [.titled, .closable, .resizable, .miniaturizable],
            backing: .buffered,
            defer: false
        )
        win.title = "Chat · \(session.projectName) (PID \(session.pid))"
        win.contentViewController = hosting
        win.center()
        win.isReleasedWhenClosed = false
        win.delegate = self
        win.identifier = NSUserInterfaceItemIdentifier("chat-\(session.pid)")
        win.minSize = NSSize(width: 420, height: 360)

        // LSUIElement apps need an explicit activation to bring a window to
        // the front. Drop back to .accessory when the window closes (see
        // windowWillClose) so we don't keep a Dock icon around.
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
        win.makeKeyAndOrderFront(nil)

        windowsByPid[session.pid] = Entry(window: win, viewModel: vm)
    }

    /// Close any open window for the given PID. Idempotent.
    func closeChat(pid: Int) {
        guard let entry = windowsByPid[pid] else { return }
        entry.viewModel.stop()
        entry.window.close()
        windowsByPid.removeValue(forKey: pid)
        restoreAccessoryIfNoWindowsLeft()
    }

    /// Tear down every chat window. Called on app quit so SSE streams stop
    /// before the URLSession infrastructure goes away.
    func closeAll() {
        for entry in windowsByPid.values {
            entry.viewModel.stop()
            entry.window.close()
        }
        windowsByPid.removeAll()
        restoreAccessoryIfNoWindowsLeft()
    }

    // MARK: NSWindowDelegate

    func windowWillClose(_ notification: Notification) {
        guard let win = notification.object as? NSWindow,
              let pid = pidForWindow(win) else { return }
        windowsByPid[pid]?.viewModel.stop()
        windowsByPid.removeValue(forKey: pid)
        restoreAccessoryIfNoWindowsLeft()
    }

    // MARK: - helpers

    private func pidForWindow(_ window: NSWindow) -> Int? {
        for (pid, entry) in windowsByPid where entry.window === window {
            return pid
        }
        return nil
    }

    private func restoreAccessoryIfNoWindowsLeft() {
        // Only return to accessory mode when no chat windows AND no welcome
        // window are visible. WelcomeController handles its own activation
        // policy on close.
        if windowsByPid.isEmpty {
            // Defer to next runloop tick so the closing window finishes its
            // own teardown before we mutate the activation policy.
            DispatchQueue.main.async {
                NSApp.setActivationPolicy(.accessory)
            }
        }
    }
}
