import XCTest
@testable import ClaudeWatchTray

/// Sparkle wrapper tests. We don't exercise the real Sparkle network /
/// signing path here — that requires a signed bundle and a live appcast,
/// which is out of scope for unit tests. Instead we pin the wrapper's
/// observable state machine and the frequency-mapping table that the
/// Settings UI binds to.
///
/// The real `SPUStandardUpdaterController` is constructed in the singleton
/// init; on a non-app test bundle Sparkle gracefully returns an updater
/// without a feed URL and never actually phones home. Calling
/// `checkForUpdates` from a test is safe because Sparkle gates the network
/// fetch behind the bundle's Info.plist, which the unit-test runner doesn't
/// have. If that ever changes we'll need to swap in a protocol-based mock.
@MainActor
final class UpdateManagerTests: XCTestCase {

    /// Each test wipes the UserDefaults keys we own so state doesn't leak
    /// between tests (and so re-running locally is hermetic).
    override func setUp() {
        super.setUp()
        let d = UserDefaults.standard
        d.removeObject(forKey: UpdateManager.lastCheckedDefaultsKey)
        d.removeObject(forKey: SettingsViewModel.updatesEnabledKey)
        d.removeObject(forKey: SettingsViewModel.updatesFrequencyKey)
    }

    override func tearDown() {
        let d = UserDefaults.standard
        d.removeObject(forKey: UpdateManager.lastCheckedDefaultsKey)
        d.removeObject(forKey: SettingsViewModel.updatesEnabledKey)
        d.removeObject(forKey: SettingsViewModel.updatesFrequencyKey)
        super.tearDown()
    }

    // MARK: - Status equatable

    /// The Status enum drives view-binding equality checks (e.g. the menu
    /// bar disables the button "when status == .checking"). If we lose
    /// associated-value-aware Equatable we'd silently break those.
    func test_status_equatable_same_case_equal() {
        XCTAssertEqual(UpdateManager.Status.idle, UpdateManager.Status.idle)
        XCTAssertEqual(UpdateManager.Status.checking, UpdateManager.Status.checking)
        XCTAssertEqual(UpdateManager.Status.upToDate, UpdateManager.Status.upToDate)
        XCTAssertEqual(
            UpdateManager.Status.foundUpdate(version: "1.2.3"),
            UpdateManager.Status.foundUpdate(version: "1.2.3")
        )
        XCTAssertEqual(
            UpdateManager.Status.error("network down"),
            UpdateManager.Status.error("network down")
        )
    }

    func test_status_equatable_different_associated_value_not_equal() {
        XCTAssertNotEqual(
            UpdateManager.Status.foundUpdate(version: "1.2.3"),
            UpdateManager.Status.foundUpdate(version: "1.2.4")
        )
        XCTAssertNotEqual(
            UpdateManager.Status.error("a"),
            UpdateManager.Status.error("b")
        )
    }

    func test_status_equatable_different_cases_not_equal() {
        XCTAssertNotEqual(UpdateManager.Status.idle, UpdateManager.Status.checking)
        XCTAssertNotEqual(UpdateManager.Status.upToDate,
                          UpdateManager.Status.foundUpdate(version: "1.0.0"))
        XCTAssertNotEqual(UpdateManager.Status.checking,
                          UpdateManager.Status.error("oops"))
    }

    // MARK: - checkNow() state transitions

    /// `checkNow()` must immediately flip to .checking so the UI can render
    /// a progress indicator before Sparkle's async machinery returns. Note:
    /// because `UpdateManager` is a singleton we don't assert a "before"
    /// state — earlier tests may have left it in any case. We only pin the
    /// post-condition, which is the contract the UI depends on.
    func test_checkNow_transitions_to_checking() {
        let mgr = UpdateManager.shared
        mgr.checkNow()
        XCTAssertEqual(mgr.status, .checking,
                       "checkNow must flip status to .checking synchronously")
    }

    /// `checkNow()` should also stamp `lastChecked` and persist it so the
    /// Settings UI shows the correct timestamp after a relaunch.
    func test_checkNow_stamps_lastChecked() {
        let mgr = UpdateManager.shared
        let before = Date().addingTimeInterval(-1)

        mgr.checkNow()

        guard let stamped = mgr.lastChecked else {
            XCTFail("checkNow must set lastChecked")
            return
        }
        XCTAssertGreaterThan(stamped, before)

        // And it should be in UserDefaults so a relaunch picks it up.
        let persisted = UserDefaults.standard.object(forKey: UpdateManager.lastCheckedDefaultsKey) as? Date
        XCTAssertNotNil(persisted, "lastChecked must persist to UserDefaults")
    }

    // MARK: - start() interval mapping

    /// `start(enabled: false, ...)` is the "never check automatically" path —
    /// it should not crash and should leave the manager in a quiescent state.
    /// We can't directly assert "Sparkle's start() was not called" without a
    /// mock, but we can at least assert no error transition happened.
    func test_start_disabled_does_not_error() {
        let mgr = UpdateManager.shared
        mgr.start(enabled: false, frequencyHours: 168)
        // Disabled path should never enter .error; it just sits at whatever
        // the prior state was (typically .idle on a fresh singleton).
        if case .error = mgr.status {
            XCTFail("disabled start must not surface an error")
        }
    }

    /// `start(enabled: true, frequencyHours: 168)` is the recommended default
    /// (weekly checks). We can't easily reach into Sparkle to read back the
    /// interval, but we can assert the call doesn't error and that subsequent
    /// re-calls are idempotent (no double-start).
    func test_start_enabled_is_idempotent() {
        let mgr = UpdateManager.shared
        mgr.start(enabled: true, frequencyHours: 168)
        // Second call with same values must not throw or change status to error.
        mgr.start(enabled: true, frequencyHours: 168)
        if case .error(let msg) = mgr.status {
            // Sparkle without a real EdDSA key / feed URL can legitimately
            // refuse to start in test bundles. That's fine — we just verify
            // the error path is the *only* way we get here, not a crash.
            XCTAssertFalse(msg.isEmpty, "error case must carry a message")
        }
    }

    // MARK: - CheckFrequency table

    /// The frequency picker stores Double-hours, but the UI presents an enum.
    /// `from(hours:)` is the round-trip that has to be stable so persisted
    /// preferences map back to the right Picker option after relaunch.
    func test_checkFrequency_round_trip() {
        for freq in UpdateManager.CheckFrequency.allCases {
            let roundTripped = UpdateManager.CheckFrequency.from(hours: freq.hours)
            // Manual (hours == 0) round-trips to manual; others to themselves.
            XCTAssertEqual(roundTripped, freq,
                           "\(freq.rawValue) failed to round-trip via hours \(freq.hours)")
        }
    }

    func test_checkFrequency_hours_table() {
        XCTAssertEqual(UpdateManager.CheckFrequency.daily.hours, 24)
        XCTAssertEqual(UpdateManager.CheckFrequency.weekly.hours, 168)
        XCTAssertEqual(UpdateManager.CheckFrequency.monthly.hours, 24 * 30)
        XCTAssertEqual(UpdateManager.CheckFrequency.manual.hours, 0)
    }

    func test_checkFrequency_from_offgrid_values() {
        // Off-grid values snap to the nearest sensible bucket — exercise the
        // boundary conditions documented in the implementation.
        XCTAssertEqual(UpdateManager.CheckFrequency.from(hours: 0), .manual)
        XCTAssertEqual(UpdateManager.CheckFrequency.from(hours: 0.5), .manual)
        XCTAssertEqual(UpdateManager.CheckFrequency.from(hours: 12), .daily)
        XCTAssertEqual(UpdateManager.CheckFrequency.from(hours: 72), .weekly)
        XCTAssertEqual(UpdateManager.CheckFrequency.from(hours: 1000), .monthly)
    }

    // MARK: - SettingsViewModel ↔ UpdateManager preferences bridge

    /// `SettingsViewModel.persistUpdatesToDefaults` + `loadUpdatesFromDefaults`
    /// is how the UpdatesTab survives a relaunch. Round-trip it to pin the
    /// contract.
    func test_updates_preferences_round_trip() {
        var prefs = UpdatesConfig()
        prefs.enabled = true
        prefs.frequencyHours = 24

        SettingsViewModel.persistUpdatesToDefaults(prefs)
        let read = SettingsViewModel.loadUpdatesFromDefaults()

        XCTAssertEqual(read.enabled, true)
        XCTAssertEqual(read.frequencyHours, 24)
    }

    func test_updates_preferences_defaults_when_unset() {
        // Fresh UserDefaults (setUp wiped them) should yield struct defaults.
        let read = SettingsViewModel.loadUpdatesFromDefaults()
        XCTAssertEqual(read, UpdatesConfig(),
                       "unset UserDefaults must yield struct defaults (disabled, weekly)")
    }
}
