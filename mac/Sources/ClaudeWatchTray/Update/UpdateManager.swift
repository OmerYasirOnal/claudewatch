import Foundation
import SwiftUI

// Sparkle's headers ship with Objective-C nullability and Sendable annotations
// that don't quite line up with Swift 6 strict concurrency on every published
// release. @preconcurrency lets us import the framework without forcing every
// call site to litter the codebase with isolation casts. Documented in
// mac/docs/sparkle-setup.md → "Strict concurrency notes".
#if canImport(Sparkle)
@preconcurrency import Sparkle
#endif

/// User-facing wrapper around Sparkle's `SPUStandardUpdaterController`.
///
/// Responsibilities:
///   1. Boot the updater with the user's saved preferences (enabled + freq)
///      so we never auto-check before the user has opted in.
///   2. Expose a small `Status` state machine to SwiftUI so the menu bar can
///      surface "checking…" / "up to date" / "found vX.Y.Z" without leaking
///      Sparkle types into the views.
///   3. Track the `lastChecked` timestamp for the Settings UI display.
///
/// IMPORTANT: this is a scaffold. It does NOT ship with a real EdDSA public
/// key (Info.plist has `REPLACE_WITH_GENERATED_KEY`). The first signed
/// release must regenerate the key per `mac/docs/sparkle-setup.md` before
/// `automaticallyChecksForUpdates` will succeed in production.
@MainActor
final class UpdateManager: NSObject, ObservableObject {

    // MARK: - Singleton

    /// Shared instance — Sparkle expects exactly one updater per bundle.
    static let shared = UpdateManager()

    // MARK: - Published state for SwiftUI

    /// Most recent successful check timestamp. `nil` means "never checked".
    /// Persisted to `UserDefaults` so it survives relaunches.
    @Published private(set) var lastChecked: Date?

    /// Coarse-grained state machine the UI binds to. Sparkle's own delegates
    /// give us much finer-grained signals; we collapse them into these five
    /// cases because that's all the menu bar / Settings really need.
    @Published private(set) var status: Status = .idle

    enum Status: Equatable {
        case idle
        case checking
        case foundUpdate(version: String)
        case upToDate
        case error(String)
    }

    // MARK: - Sparkle plumbing

    #if canImport(Sparkle)
    /// The standard controller bundles a Sparkle `SPUUpdater` with the default
    /// AppKit "you've got an update" UI. We pass `startingUpdater: false` so
    /// we can wait for user preferences to load before kicking off checks —
    /// otherwise Sparkle would fire on the very first launch, before the user
    /// even sees the welcome panel.
    private let controller: SPUStandardUpdaterController
    #endif

    /// `UserDefaults` key for `lastChecked`. Kept as a constant so tests can
    /// reach in and inspect/clear it.
    nonisolated static let lastCheckedDefaultsKey = "com.omeryasironal.claudewatch.update.lastChecked"

    /// Whether `start(...)` has already wired up Sparkle. Idempotent guard so
    /// repeated calls (e.g. when the user re-saves Settings) just update the
    /// interval/enabled flag instead of re-instantiating anything.
    private var didStart: Bool = false

    // MARK: - Init

    override init() {
        #if canImport(Sparkle)
        // No updater delegate, no userDriver delegate — Sparkle's defaults are
        // sufficient for the scaffold. We can extend later if we want custom
        // dialog copy or to override appcast filtering.
        self.controller = SPUStandardUpdaterController(
            startingUpdater: false,
            updaterDelegate: nil,
            userDriverDelegate: nil
        )
        #endif
        super.init()
        // Rehydrate `lastChecked` so the Settings UI doesn't show "Never"
        // after every launch even though we did check yesterday.
        if let ts = UserDefaults.standard.object(forKey: Self.lastCheckedDefaultsKey) as? Date {
            self.lastChecked = ts
        }
    }

    // MARK: - Lifecycle

    /// Configure Sparkle from the user's persisted preferences and (if
    /// enabled) start the periodic check loop. Safe to call multiple times —
    /// later calls just reapply the new settings without restarting the
    /// underlying updater.
    ///
    /// - Parameters:
    ///   - enabled: master switch. When false, Sparkle is fully quiet — no
    ///     background checks. The user can still trigger `checkNow()`
    ///     manually from the menu bar.
    ///   - frequencyHours: how often Sparkle should poll the appcast in the
    ///     background. Sparkle works in seconds, so we multiply by 3600. The
    ///     UI exposes Daily (24h), Weekly (168h), Monthly (~720h).
    func start(enabled: Bool, frequencyHours: Double) {
        #if canImport(Sparkle)
        let updater = controller.updater
        updater.automaticallyChecksForUpdates = enabled
        // Sparkle treats anything < 3600s as "way too aggressive" and clamps.
        // We never expose a sub-hourly option in the UI, so the multiplication
        // below is always safe.
        updater.updateCheckInterval = max(3600, frequencyHours * 3600)
        if enabled && !didStart {
            // start() can throw if the bundle is misconfigured (e.g. missing
            // SUFeedURL/SUPublicEDKey). We log + surface in `status` so the
            // UI can show the error instead of silently doing nothing.
            do {
                try updater.start()
                didStart = true
            } catch {
                status = .error("Sparkle failed to start: \(error.localizedDescription)")
            }
        }
        #else
        // No Sparkle on this platform (e.g. Linux CI). Mark as error so the
        // UI can hide / disable the "Check now" button cleanly.
        if enabled {
            status = .error("Sparkle is not available on this platform")
        }
        _ = frequencyHours
        #endif
    }

    /// User-triggered "Check for updates…" — the menu bar / Settings button
    /// hook into this. Always shows the Sparkle UI even if no update is
    /// available (so the user gets feedback). Distinct from the silent
    /// background poll, which only shows UI when an update IS available.
    func checkNow() {
        status = .checking
        // Record the attempt timestamp immediately — Sparkle's delegate
        // callbacks for completion aren't wired up in the scaffold yet, so
        // we approximate by stamping at "check started". Good enough for the
        // Settings display; can be refined later via SPUUpdaterDelegate.
        let now = Date()
        lastChecked = now
        UserDefaults.standard.set(now, forKey: Self.lastCheckedDefaultsKey)

        #if canImport(Sparkle)
        controller.checkForUpdates(nil)
        #else
        status = .error("Sparkle is not available on this platform")
        #endif
    }

    // MARK: - Frequency helpers

    /// Canonical mapping between the user-facing Picker labels and the hour
    /// values we feed to `start(...)`. Exposed publicly so the Settings UI
    /// stays in sync if we ever add a "Twice a day" tier.
    enum CheckFrequency: String, CaseIterable, Identifiable {
        case daily      // 24 hours
        case weekly     // 168 hours — our recommended default
        case monthly    // ~720 hours (30 days)
        case manual     // disabled — only `checkNow()` runs

        var id: String { rawValue }

        var hours: Double {
            switch self {
            case .daily:   return 24
            case .weekly:  return 168
            case .monthly: return 24 * 30
            case .manual:  return 0
            }
        }

        var displayName: String {
            switch self {
            case .daily:   return "Daily"
            case .weekly:  return "Weekly"
            case .monthly: return "Monthly"
            case .manual:  return "Manual only"
            }
        }

        /// Inverse of `hours` — round-trip a persisted value back to a case.
        /// Off-grid values snap to the nearest sensible bucket so we don't
        /// drop user preferences on the floor when adding new cases.
        static func from(hours: Double) -> CheckFrequency {
            switch hours {
            case ..<1:   return .manual
            case 1..<48: return .daily
            case 48..<336: return .weekly
            default: return .monthly
            }
        }
    }
}
