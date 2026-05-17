import Foundation
import SwiftUI

/// Two-stage config state: `liveConfig` is the server's last-known truth,
/// `draftConfig` is what the user is editing. The UI binds to draft only;
/// nothing is sent to the backend until the user clicks Save. This mirrors
/// the web frontend's staged-draft Save button (PR #79) — same UX problem,
/// same fix on macOS.
@MainActor
final class SettingsViewModel: ObservableObject {
    /// Last config we successfully read from (or wrote to) the backend.
    @Published var liveConfig: AppConfig = .init()
    /// The user's in-progress edits. Diverges from `liveConfig` until Save.
    @Published var draftConfig: AppConfig = .init()
    @Published var isLoading: Bool = false
    @Published var isSaving: Bool = false
    @Published var lastError: String?
    @Published var lastSavedAt: Date?

    private let api: APIClient

    init(api: APIClient = APIClient()) {
        self.api = api
    }

    /// True whenever the user has edits that haven't been persisted yet.
    var isDirty: Bool { liveConfig != draftConfig }

    func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            var cfg = try await api.getConfig()
            // The backend doesn't store the Sparkle preferences (cadence is
            // a client-only concern), so hydrate the Updates section from
            // local UserDefaults before exposing it to the UI. Without this,
            // every Settings open would show "Manual" + disabled regardless
            // of what the user actually picked.
            cfg.updates = Self.loadUpdatesFromDefaults()
            self.liveConfig = cfg
            self.draftConfig = cfg
            lastError = nil
        } catch {
            lastError = "Failed to load config: \(error)"
        }
    }

    /// Persist the draft to the backend.
    /// The backend's POST /api/config does a deep-merge; we still send only the
    /// fields we manage to minimize the chance of overriding e.g. pricing.
    /// On success the draft is promoted to live (so isDirty flips false);
    /// on failure liveConfig stays put and the draft is preserved so the user
    /// can retry or discard.
    func save() async {
        isSaving = true
        defer { isSaving = false }
        let cfg = draftConfig
        let payload: [String: Any] = [
            "port": cfg.port,
            "plan": cfg.plan,
            "read_only": cfg.readOnly,
            "privacy_mode": cfg.privacyMode,
            "show_log_text": cfg.showLogText,
            "process_scan_interval_seconds": cfg.processScanIntervalSeconds,
            "iterm_refresh_interval_seconds": cfg.itermRefreshIntervalSeconds,
            "notifications": [
                "enabled": cfg.notifications.enabled,
                "on_session_end": cfg.notifications.onSessionEnd,
                "on_high_cost": cfg.notifications.onHighCost,
                "cost_threshold_usd": cfg.notifications.costThresholdUsd,
            ],
            "remote_control": [
                "enabled": cfg.remoteControl.enabled,
            ],
            "editor": [
                "enabled": cfg.editor.enabled,
                "command": cfg.editor.command,
            ],
        ]
        do {
            try await api.postConfig(payload)
            // Promote draft to live — but use the snapshot we sent, not the
            // current draftConfig: the user may have kept typing while the
            // POST was in flight, and we don't want those further-edits to
            // be silently treated as already-saved.
            self.liveConfig = cfg
            self.lastSavedAt = Date()
            self.lastError = nil
            // Persist Sparkle prefs locally (backend doesn't know about them)
            // and push them into UpdateManager so the next scheduled check
            // picks up the new cadence without an app relaunch.
            Self.persistUpdatesToDefaults(cfg.updates)
            UpdateManager.shared.start(
                enabled: cfg.updates.enabled,
                frequencyHours: cfg.updates.frequencyHours
            )
        } catch {
            lastError = "Save failed: \(error)"
        }
    }

    // MARK: - Updates persistence

    /// `UserDefaults` keys for the Sparkle preferences. Constants so the
    /// tests can reach in and reset / inspect state.
    static let updatesEnabledKey = "com.omeryasironal.claudewatch.update.enabled"
    static let updatesFrequencyKey = "com.omeryasironal.claudewatch.update.frequencyHours"

    /// Re-read Sparkle preferences from UserDefaults. First-run defaults
    /// match `UpdatesConfig()` — disabled, weekly cadence.
    static func loadUpdatesFromDefaults() -> UpdatesConfig {
        var c = UpdatesConfig()
        let d = UserDefaults.standard
        // `object(forKey:)` so we can distinguish "never set" from "set to
        // false"; .bool(forKey:) would conflate them and force the toggle
        // off after a save-disable round trip is impossible to detect.
        if let enabled = d.object(forKey: updatesEnabledKey) as? Bool {
            c.enabled = enabled
        }
        if let freq = d.object(forKey: updatesFrequencyKey) as? Double, freq > 0 {
            c.frequencyHours = freq
        }
        return c
    }

    /// Mirror of `loadUpdatesFromDefaults` — called from `save()` after a
    /// successful backend write so the two halves of the form persist in one
    /// atomic-feeling step from the user's perspective.
    static func persistUpdatesToDefaults(_ updates: UpdatesConfig) {
        let d = UserDefaults.standard
        d.set(updates.enabled, forKey: updatesEnabledKey)
        d.set(updates.frequencyHours, forKey: updatesFrequencyKey)
    }

    /// Reset the draft to whatever the backend most recently confirmed.
    func discard() {
        self.draftConfig = self.liveConfig
        self.lastError = nil
    }
}
