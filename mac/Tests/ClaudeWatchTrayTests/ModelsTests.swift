import XCTest
@testable import ClaudeWatchTray

/// JSON shapes here are copied verbatim from real backend responses
/// (see `backend/models.py::ClaudeSession` and `backend/api/health.py`).
final class ModelsTests: XCTestCase {
    private func makeDecoder() -> JSONDecoder {
        let d = JSONDecoder()
        // Mirror APIClient's date strategy: tolerate optional fractional seconds.
        d.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let raw = try container.decode(String.self)
            let iso = ISO8601DateFormatter()
            iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            if let date = iso.date(from: raw) { return date }
            iso.formatOptions = [.withInternetDateTime]
            if let date = iso.date(from: raw) { return date }
            throw DecodingError.dataCorruptedError(
                in: container, debugDescription: "Bad date: \(raw)")
        }
        return d
    }

    func testSessionDecodesMinimalJSON() throws {
        let json = """
        {
          "pid": 1,
          "cwd": "/x",
          "started_at": "2026-05-16T18:00:00Z",
          "duration_seconds": 10,
          "status": "working",
          "location_type": "iterm",
          "message_count": 1,
          "is_in_flight": false
        }
        """.data(using: .utf8)!

        let s = try makeDecoder().decode(Session.self, from: json)
        XCTAssertEqual(s.pid, 1)
        XCTAssertEqual(s.cwd, "/x")
        XCTAssertEqual(s.durationSeconds, 10)
        XCTAssertEqual(s.status, "working")
        XCTAssertEqual(s.locationType, "iterm")
        XCTAssertEqual(s.messageCount, 1)
        XCTAssertFalse(s.isInFlight)
        XCTAssertNil(s.usage)
        XCTAssertNil(s.model)
        XCTAssertNil(s.currentTaskSubject)
        XCTAssertNil(s.itermSessionId)
        XCTAssertEqual(s.id, 1)
        XCTAssertEqual(s.costEstimate, 0)
        XCTAssertEqual(s.totalTokens, 0)
    }

    func testSessionDecodesISO8601WithFractionalSeconds() throws {
        let json = """
        {
          "pid": 42,
          "cwd": "/x",
          "started_at": "2026-05-16T18:00:00.123Z",
          "duration_seconds": 10,
          "status": "working",
          "location_type": "iterm",
          "message_count": 1,
          "is_in_flight": false
        }
        """.data(using: .utf8)!

        let s = try makeDecoder().decode(Session.self, from: json)
        // Parse the same string with our reference formatter for comparison.
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let expected = iso.date(from: "2026-05-16T18:00:00.123Z")
        XCTAssertNotNil(expected)
        XCTAssertEqual(s.startedAt, expected)
    }

    func testSessionDecodesNullableFields() throws {
        let json = """
        {
          "pid": 7,
          "cwd": "/x",
          "started_at": "2026-05-16T18:00:00Z",
          "duration_seconds": 10,
          "status": "waiting",
          "location_type": "headless",
          "iterm_session_id": null,
          "iterm_tab_title": null,
          "model": null,
          "message_count": 0,
          "usage": null,
          "current_task_subject": null,
          "is_in_flight": false
        }
        """.data(using: .utf8)!

        XCTAssertNoThrow(try makeDecoder().decode(Session.self, from: json))
        let s = try makeDecoder().decode(Session.self, from: json)
        XCTAssertNil(s.itermSessionId)
        XCTAssertNil(s.itermTabTitle)
        XCTAssertNil(s.model)
        XCTAssertNil(s.usage)
        XCTAssertNil(s.currentTaskSubject)
    }

    func testSessionProjectNameStripsPath() throws {
        let json = """
        {
          "pid": 1,
          "cwd": "/Users/x/Projects/foo",
          "started_at": "2026-05-16T18:00:00Z",
          "duration_seconds": 10,
          "status": "working",
          "location_type": "iterm",
          "message_count": 1,
          "is_in_flight": false
        }
        """.data(using: .utf8)!

        let s = try makeDecoder().decode(Session.self, from: json)
        XCTAssertEqual(s.projectName, "foo")
    }

    func testHealthReportDecodes() throws {
        let json = """
        {
          "iterm_api": true,
          "tmux_available": false,
          "log_dir_found": true,
          "issues": ["tmux not installed"]
        }
        """.data(using: .utf8)!

        let h = try makeDecoder().decode(HealthReport.self, from: json)
        XCTAssertTrue(h.itermApi)
        XCTAssertFalse(h.tmuxAvailable)
        XCTAssertTrue(h.logDirFound)
        XCTAssertEqual(h.issues, ["tmux not installed"])
    }
}
