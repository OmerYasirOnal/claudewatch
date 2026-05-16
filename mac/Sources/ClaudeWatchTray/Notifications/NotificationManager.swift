import AppKit
import Foundation
import OSLog
import UserNotifications

/// Owner of the native UNUserNotificationCenter integration.
///
/// When the user selects the "Native" notification source in Settings, the
/// tray subscribes to /api/stream and routes `session.ended` / `session.updated`
/// events through this manager. Two categories are registered:
///
///   - `HIGH_COST`     — Focus + Halt action buttons (cost threshold crossed)
///   - `SESSION_ENDED` — informational; no actions (PID is gone, nothing to do)
///
/// Per-PID dedup mirrors the backend's `notified_high_cost_pids` semantics so
/// the user gets at most one high-cost ping per session.
@MainActor
final class NotificationManager: NSObject, ObservableObject, UNUserNotificationCenterDelegate {
    static let shared = NotificationManager()

    /// Authorization state surfaced to the Settings UI.
    @Published private(set) var authorized: Bool = false

    /// Transient pause flag — set by the "Pause notifications for 1 hour" /
    /// "Pause until next launch" quick actions. Cleared automatically when
    /// `pausedUntil` elapses (a permanent pause uses `.distantFuture`).
    @Published private(set) var pausedUntil: Date?

    private let api: APIClient
    private var notifiedHighCostPids = Set<Int>()
    private let logger = Logger(subsystem: "com.omeryasironal.claudewatch.tray",
                                category: "NotificationManager")

    private override init() {
        self.api = APIClient()
        super.init()
        UNUserNotificationCenter.current().delegate = self
        registerCategories()
        Task { await refreshAuthorizationStatus() }
    }

    // MARK: - Authorization

    /// Ask macOS for permission to show banner/sound notifications. Safe to
    /// call repeatedly — UN caches the user's choice and returns it without
    /// re-prompting after the first decision.
    func requestAuthorization() async {
        do {
            let granted = try await UNUserNotificationCenter.current()
                .requestAuthorization(options: [.alert, .sound, .badge])
            authorized = granted
        } catch {
            logger.error("Notification authorization failed: \(error.localizedDescription)")
            authorized = false
        }
    }

    /// Read the current authorization state without prompting. Used to keep
    /// the Settings UI in sync when the user toggles permissions in System
    /// Settings while the app is running.
    ///
    /// `UNNotificationSettings` is not Sendable, so we bridge through a
    /// nonisolated continuation-style call and only carry the resolved status
    /// enum back across the MainActor boundary.
    func refreshAuthorizationStatus() async {
        let status: UNAuthorizationStatus = await withCheckedContinuation { cont in
            UNUserNotificationCenter.current().getNotificationSettings { settings in
                cont.resume(returning: settings.authorizationStatus)
            }
        }
        authorized = (status == .authorized || status == .provisional)
    }

    // MARK: - Pause control

    /// Suppress notifications for the given interval. Use `.infinity`-ish
    /// values (e.g. `.distantFuture`) for "until next launch".
    func pause(for interval: TimeInterval) {
        pausedUntil = Date().addingTimeInterval(interval)
    }

    func pauseUntilNextLaunch() {
        pausedUntil = .distantFuture
    }

    func resume() {
        pausedUntil = nil
    }

    private var isPaused: Bool {
        guard let until = pausedUntil else { return false }
        if Date() >= until {
            // Auto-clear expired pause so the UI reflects reality.
            pausedUntil = nil
            return false
        }
        return true
    }

    // MARK: - Event handlers (called from SSESubscriber)

    /// Fire a high-cost notification once per PID when the threshold is crossed.
    /// Honors the user's `on_high_cost` toggle + master enable, mirroring the
    /// backend's logic so the user doesn't get duplicates when both sources are
    /// momentarily live.
    func handleSessionUpdated(_ session: Session,
                              config: NotificationConfig) async {
        guard config.enabled, config.onHighCost, !isPaused else { return }
        let cost = session.costEstimate
        guard cost >= config.costThresholdUsd, cost > 0 else { return }
        guard !notifiedHighCostPids.contains(session.pid) else { return }
        notifiedHighCostPids.insert(session.pid)

        let content = UNMutableNotificationContent()
        content.title = "Claude session crossed $\(String(format: "%.2f", config.costThresholdUsd))"
        content.subtitle = session.projectName
        content.body = "PID \(session.pid) · $\(String(format: "%.2f", cost))"
        content.sound = .default
        content.categoryIdentifier = "HIGH_COST"
        content.userInfo = ["pid": session.pid]
        // Group by PID so a later high-cost re-fire for the same session (if we
        // ever lift the dedup) replaces the existing banner rather than stacking.
        content.threadIdentifier = "claudewatch-cost-\(session.pid)"

        await deliver(content, identifier: "high-cost-\(session.pid)")
    }

    /// Fire an informational notification when a session ends. No actions —
    /// the PID is gone so Focus/Halt would 404.
    func handleSessionEnded(pid: Int,
                            projectName: String?,
                            config: NotificationConfig) async {
        guard config.enabled, config.onSessionEnd, !isPaused else { return }
        // Free the dedup slot so if a new claude session reuses the same PID
        // (rare but possible) we'll notify on the next threshold crossing.
        notifiedHighCostPids.remove(pid)

        let content = UNMutableNotificationContent()
        content.title = "Claude session ended"
        content.subtitle = projectName ?? ""
        content.body = "PID \(pid)"
        content.sound = .default
        content.categoryIdentifier = "SESSION_ENDED"
        content.userInfo = ["pid": pid]
        content.threadIdentifier = "claudewatch-end-\(pid)"

        await deliver(content, identifier: "session-ended-\(pid)")
    }

    /// Forget any per-PID dedup state. Called when the user toggles back to
    /// the "Native" source after using "Backend" for a while so a still-running
    /// over-threshold session can re-notify under the new source.
    func resetDedup() {
        notifiedHighCostPids.removeAll()
    }

    // MARK: - Categories

    private func registerCategories() {
        let focus = UNNotificationAction(
            identifier: "FOCUS",
            title: "Focus",
            options: [.foreground]
        )
        let halt = UNNotificationAction(
            identifier: "HALT",
            title: "Halt",
            options: [.destructive]
        )
        let highCost = UNNotificationCategory(
            identifier: "HIGH_COST",
            actions: [focus, halt],
            intentIdentifiers: [],
            options: []
        )
        let ended = UNNotificationCategory(
            identifier: "SESSION_ENDED",
            actions: [],
            intentIdentifiers: [],
            options: []
        )
        UNUserNotificationCenter.current().setNotificationCategories([highCost, ended])
    }

    private func deliver(_ content: UNNotificationContent, identifier: String) async {
        let request = UNNotificationRequest(
            identifier: identifier,
            content: content,
            trigger: nil  // deliver immediately
        )
        do {
            try await UNUserNotificationCenter.current().add(request)
        } catch {
            logger.error("Failed to deliver notification \(identifier): \(error.localizedDescription)")
        }
    }

    // MARK: - UNUserNotificationCenterDelegate

    /// Route action button taps to the API. We treat `actionIdentifier` other
    /// than FOCUS/HALT as a plain "user opened the notification" gesture —
    /// in that case we just bring the app forward.
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter,
                                            didReceive response: UNNotificationResponse) async {
        let userInfo = response.notification.request.content.userInfo
        // Notification userInfo round-trips through plist; ints come back as NSNumber.
        let pid: Int? = (userInfo["pid"] as? Int)
            ?? (userInfo["pid"] as? NSNumber)?.intValue
        let action = response.actionIdentifier
        let api = APIClient()

        switch action {
        case "FOCUS":
            if let pid {
                do { try await api.focus(pid) } catch {
                    // Swallow; we can't usefully surface this from a notification handler.
                }
            }
        case "HALT":
            if let pid {
                do { try await api.halt(pid) } catch {
                    // Swallow as above.
                }
            }
        case UNNotificationDefaultActionIdentifier:
            // User tapped the notification body — bring the app forward so the
            // popover is reachable.
            await MainActor.run { NSApp.activate(ignoringOtherApps: true) }
        default:
            break
        }
    }

    /// Show the banner even when the app is "frontmost" — for a menu-bar
    /// (LSUIElement) app there's no real frontmost state, but UN still applies
    /// the foreground suppression rule, so opt in explicitly.
    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification
    ) async -> UNNotificationPresentationOptions {
        return [.banner, .sound]
    }
}
