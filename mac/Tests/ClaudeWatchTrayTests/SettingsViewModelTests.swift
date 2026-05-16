import XCTest
@testable import ClaudeWatchTray

/// Pins the staged-draft contract on SettingsViewModel:
/// liveConfig tracks the server, draftConfig tracks user edits, isDirty is
/// strictly liveConfig != draftConfig, and save() only promotes draft → live
/// on success. Mirrors the web-side guarantees from PR #79.
@MainActor
final class SettingsViewModelTests: XCTestCase {

    private func makeSession() -> URLSession {
        let cfg = URLSessionConfiguration.ephemeral
        cfg.protocolClasses = [MockURLProtocol.self]
        cfg.timeoutIntervalForRequest = 2
        cfg.timeoutIntervalForResource = 2
        return URLSession(configuration: cfg)
    }

    /// JSON body that the backend would return for GET /api/config — same
    /// snake_case shape the real API emits.
    private let configBody: Data = """
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
      "remote_control": { "enabled": false },
      "editor": { "enabled": false, "command": "code" }
    }
    """.data(using: .utf8)!

    override func tearDown() {
        MockURLProtocol.handler = nil
        super.tearDown()
    }

    // MARK: - load()

    func test_load_syncs_live_and_draft() async {
        let body = configBody
        MockURLProtocol.handler = { req in
            XCTAssertEqual(req.url?.path, "/api/config")
            let resp = HTTPURLResponse(url: req.url!, statusCode: 200,
                                       httpVersion: "HTTP/1.1", headerFields: nil)!
            return (resp, body)
        }

        let vm = SettingsViewModel(api: APIClient(session: makeSession()))
        await vm.load()

        XCTAssertEqual(vm.liveConfig, vm.draftConfig,
                       "load() must seed draft from live so the form starts clean")
        XCTAssertFalse(vm.isDirty)
        XCTAssertNil(vm.lastError)
        XCTAssertEqual(vm.draftConfig.plan, "api")
    }

    // MARK: - isDirty

    func test_isDirty_when_draft_diverges() async {
        MockURLProtocol.handler = { req in
            let resp = HTTPURLResponse(url: req.url!, statusCode: 200,
                                       httpVersion: "HTTP/1.1", headerFields: nil)!
            return (resp, self.configBody)
        }
        let vm = SettingsViewModel(api: APIClient(session: makeSession()))
        await vm.load()
        XCTAssertFalse(vm.isDirty)

        vm.draftConfig.plan = "pro"
        XCTAssertTrue(vm.isDirty)
        XCTAssertEqual(vm.liveConfig.plan, "api", "live must not move until save")
    }

    // MARK: - discard()

    func test_discard_reverts_draft() async {
        MockURLProtocol.handler = { req in
            let resp = HTTPURLResponse(url: req.url!, statusCode: 200,
                                       httpVersion: "HTTP/1.1", headerFields: nil)!
            return (resp, self.configBody)
        }
        let vm = SettingsViewModel(api: APIClient(session: makeSession()))
        await vm.load()

        vm.draftConfig.plan = "max_20x"
        vm.draftConfig.notifications.costThresholdUsd = 99
        XCTAssertTrue(vm.isDirty)

        vm.discard()
        XCTAssertEqual(vm.draftConfig, vm.liveConfig)
        XCTAssertFalse(vm.isDirty)
        XCTAssertEqual(vm.draftConfig.plan, "api")
        XCTAssertEqual(vm.draftConfig.notifications.costThresholdUsd, 5.0)
    }

    // MARK: - save() success

    func test_save_promotes_draft_on_success() async {
        // Two-phase mock: first call is GET (load), second is POST (save).
        let body = configBody
        let phase = MockPhase()
        MockURLProtocol.handler = { req in
            phase.count += 1
            let resp = HTTPURLResponse(url: req.url!, statusCode: 200,
                                       httpVersion: "HTTP/1.1", headerFields: nil)!
            // First request is GET /api/config (load), second is POST save.
            // Backend POST handler returns nothing useful for our purposes —
            // SettingsViewModel doesn't read the POST body, it just checks
            // status. An empty 200 is sufficient.
            return (resp, req.httpMethod == "GET" ? body : Data())
        }

        let vm = SettingsViewModel(api: APIClient(session: makeSession()))
        await vm.load()
        vm.draftConfig.plan = "pro"
        vm.draftConfig.editor.command = "cursor"
        XCTAssertTrue(vm.isDirty)

        await vm.save()

        XCTAssertEqual(vm.liveConfig, vm.draftConfig,
                       "save() success must promote draft → live")
        XCTAssertFalse(vm.isDirty)
        XCTAssertEqual(vm.liveConfig.plan, "pro")
        XCTAssertEqual(vm.liveConfig.editor.command, "cursor")
        XCTAssertNotNil(vm.lastSavedAt)
        XCTAssertNil(vm.lastError)
        XCTAssertEqual(phase.count, 2, "expected one GET + one POST")
    }

    // MARK: - save() failure

    func test_save_keeps_draft_on_failure() async {
        MockURLProtocol.handler = { req in
            if req.httpMethod == "GET" {
                let ok = HTTPURLResponse(url: req.url!, statusCode: 200,
                                         httpVersion: "HTTP/1.1", headerFields: nil)!
                return (ok, self.configBody)
            } else {
                // FastAPI's typical pydantic validation failure shape.
                let bad = HTTPURLResponse(url: req.url!, statusCode: 422,
                                          httpVersion: "HTTP/1.1", headerFields: nil)!
                let body = #"{"detail":"bad"}"#.data(using: .utf8)!
                return (bad, body)
            }
        }

        let vm = SettingsViewModel(api: APIClient(session: makeSession()))
        await vm.load()
        let originalLive = vm.liveConfig
        vm.draftConfig.plan = "team"
        XCTAssertTrue(vm.isDirty)

        await vm.save()

        // Live must NOT have moved — that's the whole point of a draft.
        XCTAssertEqual(vm.liveConfig, originalLive,
                       "failed save() must leave liveConfig untouched")
        XCTAssertEqual(vm.draftConfig.plan, "team",
                       "draft is preserved so the user can retry or discard")
        XCTAssertTrue(vm.isDirty)
        XCTAssertNotNil(vm.lastError)
    }
}

/// Sharable counter for multi-step mock handlers.
private final class MockPhase: @unchecked Sendable {
    var count: Int = 0
}
