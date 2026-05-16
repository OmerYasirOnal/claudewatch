import Foundation
import SwiftUI

@MainActor
final class SettingsViewModel: ObservableObject {
    @Published var config: AppConfig = .init()
    @Published var isLoading: Bool = false
    @Published var lastError: String?
    @Published var lastSavedAt: Date?

    private let api: APIClient

    init(api: APIClient = APIClient()) {
        self.api = api
    }

    func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            let cfg = try await api.getConfig()
            config = cfg
            lastError = nil
        } catch {
            lastError = "Failed to load config: \(error)"
        }
    }

    /// Persist the entire config block to the backend.
    /// The backend's POST /api/config does a deep-merge; we still send only the
    /// fields we manage to minimize the chance of overriding e.g. pricing.
    func save() async {
        let payload: [String: Any] = [
            "port": config.port,
            "plan": config.plan,
            "read_only": config.readOnly,
            "privacy_mode": config.privacyMode,
            "show_log_text": config.showLogText,
            "process_scan_interval_seconds": config.processScanIntervalSeconds,
            "iterm_refresh_interval_seconds": config.itermRefreshIntervalSeconds,
            "notifications": [
                "enabled": config.notifications.enabled,
                "on_session_end": config.notifications.onSessionEnd,
                "on_high_cost": config.notifications.onHighCost,
                "cost_threshold_usd": config.notifications.costThresholdUsd,
            ],
            "remote_control": [
                "enabled": config.remoteControl.enabled,
            ],
            "editor": [
                "enabled": config.editor.enabled,
                "command": config.editor.command,
            ],
        ]
        do {
            try await api.postConfig(payload)
            lastSavedAt = Date()
            lastError = nil
        } catch {
            lastError = "Save failed: \(error)"
        }
    }
}
