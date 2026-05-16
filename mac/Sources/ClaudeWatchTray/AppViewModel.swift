import AppKit
import Foundation
import SwiftUI

/// Notification source the tray honors.
///
///   - `.backend` (default): backend fires osascript-based notifications, no
///     buttons. Tray does nothing extra.
///   - `.native`: tray subscribes to /api/stream + raises actionable
///     UNUserNotificationCenter banners with Focus/Halt buttons. The backend
///     is told to stop firing its own osascripts (via POST /api/config) so the
///     user doesn't get duplicates.
enum NotificationSource: String, Codable {
    case backend
    case native
}

/// Persisted under this UserDefaults key. Defaults to `.backend` for upgrade
/// compatibility — existing users keep the behavior they signed up for.
private let kNotificationSourceKey = "claudewatch.tray.notificationSource"

/// State + polling for the menu bar app.
@MainActor
final class AppViewModel: ObservableObject {
    @Published var sessions: [Session] = []
    @Published var health: HealthReport?
    @Published var lastError: String?
    @Published var lastUpdated: Date?
    @Published var notificationSource: NotificationSource {
        didSet {
            UserDefaults.standard.set(notificationSource.rawValue, forKey: kNotificationSourceKey)
            Task { await applyNotificationSource() }
        }
    }
    /// Cached notification preferences from /api/config. The tray-side
    /// NotificationManager consults these when deciding whether to fire.
    @Published var notificationConfig: NotificationConfig = .init()

    private let api = APIClient()
    private var pollTask: Task<Void, Never>?
    private let sseSubscriber = SSESubscriber()

    var activeCount: Int { sessions.count }
    var totalCost: Double {
        sessions.map(\.costEstimate).reduce(0, +)
    }
    var menuBarLabel: String {
        if activeCount == 0 { return "" }
        if totalCost > 0 {
            return "\(activeCount) · $\(String(format: "%.2f", totalCost))"
        }
        return "\(activeCount)"
    }

    init() {
        let raw = UserDefaults.standard.string(forKey: kNotificationSourceKey) ?? ""
        self.notificationSource = NotificationSource(rawValue: raw) ?? .backend
        wireSSE()
        start()
        // If we booted in native mode, kick off the pipeline (auth + subscribe
        // + tell backend to hush its osascripts). Fire-and-forget; the actual
        // subscriber is robust to backend not being up yet (it'll backoff).
        if notificationSource == .native {
            Task { await applyNotificationSource() }
        }
    }

    deinit {
        pollTask?.cancel()
    }

    func start() {
        pollTask?.cancel()
        pollTask = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                await self?.refresh()
                try? await Task.sleep(for: .seconds(3))
            }
        }
    }

    func refresh() async {
        do {
            sessions = try await api.listSessions()
                .sorted { ($0.costEstimate, $0.pid) > ($1.costEstimate, $1.pid) }
            lastError = nil
            lastUpdated = Date()
            // Health is cheaper than sessions; refresh occasionally.
            if Int.random(in: 0..<5) == 0 {
                if let h = try? await api.health() { health = h }
            }
            // Pick up notification prefs so NotificationManager honors any
            // toggles the user flipped in Settings.
            if let cfg = try? await api.getConfig() {
                notificationConfig = cfg.notifications
            }
        } catch APIError.transport {
            lastError = "Backend unreachable. Run: claudewatch start --daemon"
            sessions = []
        } catch {
            lastError = "API error: \(error)"
        }
    }

    func focus(_ pid: Int) {
        Task {
            do { try await api.focus(pid) }
            catch { lastError = "Focus failed: \(error)" }
        }
    }

    func halt(_ pid: Int) {
        Task {
            do { try await api.halt(pid) }
            catch { lastError = "Halt failed: \(error)" }
        }
    }

    func openChat(for session: Session) {
        ChatWindowController.shared.openChat(for: session)
    }

    func openDashboard() {
        if let url = URL(string: "http://127.0.0.1:7788/") {
            NSWorkspace.shared.open(url)
        }
    }

    /// Opens the native Settings window. Uses the @Environment(\\.openSettings)
    /// path via Scene's Settings — implemented by closing the menu bar popover
    /// and sending the standard Settings menu action.
    func openSettings() {
        NSApp.activate(ignoringOtherApps: true)
        // macOS 14+ uses showSettingsWindow:; older uses showPreferencesWindow:
        if NSApp.responds(to: Selector(("showSettingsWindow:"))) {
            NSApp.sendAction(Selector(("showSettingsWindow:")), to: nil, from: nil)
        } else {
            NSApp.sendAction(Selector(("showPreferencesWindow:")), to: nil, from: nil)
        }
    }

    func quit() {
        ChatWindowController.shared.closeAll()
        NSApplication.shared.terminate(nil)
    }

    // MARK: - Notification source plumbing

    private func wireSSE() {
        sseSubscriber.onEvent = { [weak self] event in
            guard let self else { return }
            let cfg = self.notificationConfig
            switch event {
            case .snapshot:
                break
            case .sessionStarted:
                break
            case .sessionUpdated(let sess):
                Task { await NotificationManager.shared.handleSessionUpdated(sess, config: cfg) }
            case .sessionEnded(let pid, let projectName):
                Task { await NotificationManager.shared.handleSessionEnded(pid: pid,
                                                                            projectName: projectName,
                                                                            config: cfg) }
            }
        }
    }

    /// Transition between notification modes. Order matters:
    ///   - Backend → Native: ask for permission FIRST (so we don't subscribe
    ///     and then drop events while waiting on the prompt), then tell the
    ///     backend to stop, then subscribe.
    ///   - Native → Backend: stop the SSE consumer, then re-enable the
    ///     backend's osascripts. Either order is safe but doing the unsub
    ///     first avoids a moment of double-delivery.
    func applyNotificationSource() async {
        switch notificationSource {
        case .native:
            await NotificationManager.shared.requestAuthorization()
            NotificationManager.shared.resetDedup()
            // Tell the backend to stand down. The user can re-enable in
            // Settings if they want both for some reason; we don't enforce.
            do {
                try await api.postConfig(["notifications": ["enabled": false]])
            } catch {
                lastError = "Could not disable backend notifications: \(error)"
            }
            sseSubscriber.start()
        case .backend:
            sseSubscriber.stop()
            do {
                try await api.postConfig(["notifications": ["enabled": true]])
            } catch {
                lastError = "Could not re-enable backend notifications: \(error)"
            }
        }
    }
}
