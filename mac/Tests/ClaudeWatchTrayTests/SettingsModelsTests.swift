import XCTest
@testable import ClaudeWatchTray

/// AppConfig's decoder is intentionally lenient — every field falls back to
/// the struct's default if missing or the wrong shape. These tests pin that
/// behavior so a future "fix" doesn't accidentally make config decoding strict.
final class SettingsModelsTests: XCTestCase {

    func testAppConfigDecodesFromBackendDefaults() throws {
        // Mirrors the camelCase-keyed wire shape coming back from
        // backend/config.py::DEFAULT_CONFIG (snake_case → CodingKeys).
        let json = """
        {
          "port": 7788,
          "plan": "api",
          "read_only": false,
          "privacy_mode": true,
          "show_log_text": false,
          "process_scan_interval_seconds": 2,
          "iterm_refresh_interval_seconds": 5,
          "notifications": {
            "enabled": true,
            "on_session_end": true,
            "on_high_cost": true,
            "cost_threshold_usd": 5.0
          },
          "remote_control": {
            "enabled": false
          },
          "editor": {
            "enabled": false,
            "command": "code"
          }
        }
        """.data(using: .utf8)!

        let cfg = try JSONDecoder().decode(AppConfig.self, from: json)
        XCTAssertEqual(cfg.port, 7788)
        XCTAssertEqual(cfg.plan, "api")
        XCTAssertFalse(cfg.readOnly)
        XCTAssertTrue(cfg.privacyMode)
        XCTAssertEqual(cfg.processScanIntervalSeconds, 2)
        XCTAssertEqual(cfg.itermRefreshIntervalSeconds, 5)
        XCTAssertTrue(cfg.notifications.enabled)
        XCTAssertTrue(cfg.notifications.onSessionEnd)
        XCTAssertTrue(cfg.notifications.onHighCost)
        XCTAssertEqual(cfg.notifications.costThresholdUsd, 5.0)
        XCTAssertFalse(cfg.remoteControl.enabled)
        XCTAssertFalse(cfg.editor.enabled)
        XCTAssertEqual(cfg.editor.command, "code")
    }

    func testAppConfigTolerantOfMissingFields() throws {
        let json = "{}".data(using: .utf8)!
        let cfg = try JSONDecoder().decode(AppConfig.self, from: json)
        // All fields keep their struct defaults.
        XCTAssertEqual(cfg.port, 7788)
        XCTAssertEqual(cfg.plan, "api")
        XCTAssertFalse(cfg.readOnly)
        XCTAssertTrue(cfg.privacyMode)
        XCTAssertFalse(cfg.showLogText)
        XCTAssertEqual(cfg.processScanIntervalSeconds, 2)
        XCTAssertEqual(cfg.itermRefreshIntervalSeconds, 5)
        XCTAssertEqual(cfg.notifications, NotificationConfig())
        XCTAssertEqual(cfg.remoteControl, RemoteControlConfig())
        XCTAssertEqual(cfg.editor, EditorConfig())
        XCTAssertEqual(cfg.updates, UpdatesConfig())
    }

    func testUpdatesConfigDecodesFromBackend() throws {
        // The backend doesn't actually emit this section today (the Sparkle
        // cadence is a client-only setting persisted to UserDefaults), but
        // we still want the decoder to tolerate it if it ever does — and
        // to map snake_case → camelCase the same way the other sections do.
        let json = """
        {
          "updates": { "enabled": true, "frequency_hours": 24 }
        }
        """.data(using: .utf8)!
        let cfg = try JSONDecoder().decode(AppConfig.self, from: json)
        XCTAssertTrue(cfg.updates.enabled)
        XCTAssertEqual(cfg.updates.frequencyHours, 24)
    }

    func testAppConfigTolerantOfWrongTypes() throws {
        let json = """
        {"port": "not a number", "plan": 42, "privacy_mode": "yes"}
        """.data(using: .utf8)!
        let cfg = try JSONDecoder().decode(AppConfig.self, from: json)
        // Each bad-type field falls back to the struct default rather than throwing.
        XCTAssertEqual(cfg.port, 7788)
        XCTAssertEqual(cfg.plan, "api")
        XCTAssertTrue(cfg.privacyMode)
    }
}
