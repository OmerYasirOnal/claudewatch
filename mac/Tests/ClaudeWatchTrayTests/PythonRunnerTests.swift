import XCTest
import Foundation
import Network
@testable import ClaudeWatchTray

/// PythonRunner is hard to unit-test in isolation because it actually launches a
/// subprocess and probes a TCP port. We exercise the two pure decision branches:
///   1. Port already busy → state becomes .external.
///   2. Port free + no bundled python on disk → state ends in .failed(...).
///
/// Both tests are integration-flavored; mark them XCTSkip in environments
/// where they can't run cleanly (sandboxed CI, dev boxes that DO have a
/// bundled Python).
@MainActor
final class PythonRunnerTests: XCTestCase {

    /// Bind a TCP listener to an OS-assigned free port, return (port, listener)
    /// so the test can keep the listener alive for the duration of the call.
    private func bindFreePort() throws -> (Int, NWListener) {
        let listener = try NWListener(using: .tcp, on: .any)
        listener.newConnectionHandler = { conn in
            // Accept + immediately close — we just need /api/health to look "up".
            conn.start(queue: .global())
            // Respond with a minimal HTTP 200 so isBackendUp() returns true.
            let httpResp = "HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}"
            conn.send(content: httpResp.data(using: .utf8),
                      completion: .contentProcessed { _ in
                conn.cancel()
            })
        }

        let started = expectation(description: "listener ready")
        listener.stateUpdateHandler = { state in
            if case .ready = state { started.fulfill() }
        }
        listener.start(queue: .global())
        wait(for: [started], timeout: 5)

        guard let port = listener.port?.rawValue else {
            throw NSError(domain: "test", code: 1)
        }
        return (Int(port), listener)
    }

    func testStartIfNeededReturnsExternalWhenPortBusy() async throws {
        let (port, listener) = try bindFreePort()
        defer { listener.cancel() }

        let runner = PythonRunner(port: port)
        await runner.startIfNeeded()

        if case .external = runner.state {
            // success
        } else {
            XCTFail("expected .external, got \(runner.state)")
        }
    }

    func testStartIfNeededFailsCleanlyWhenNoBundle() async throws {
        // Find a port that's free *right now*. There's a TOCTOU window between
        // discovery and startIfNeeded(); accept that the test could occasionally
        // hit .external instead — in which case skip rather than fail.
        let (port, listener) = try bindFreePort()
        listener.cancel()
        // Brief pause so the OS releases the socket before PythonRunner probes.
        try await Task.sleep(for: .milliseconds(50))

        // Skip if the dev machine has a bundled Python at the expected path —
        // PythonRunner would then succeed and break this test. Detect that by
        // checking the same candidates PythonRunner walks.
        if Self.bundledPythonExists() {
            throw XCTSkip("Skipping: bundled Python present at mac/build/python — this branch only verifies the no-bundle failure path.")
        }

        let runner = PythonRunner(port: port)
        await runner.startIfNeeded()

        switch runner.state {
        case .failed:
            // success
            break
        case .external:
            throw XCTSkip("Race: port was reused before runner could probe; not a regression.")
        default:
            XCTFail("expected .failed, got \(runner.state)")
        }
    }

    // MARK: - orphan PID-file handling (audit #94)

    /// pidFileURL must live under ~/Library/Caches/<bundle-id>/ so it's
    /// auto-cleaned by macOS and doesn't pollute Application Support.
    func test_pidFileURL_path_under_caches() {
        let runner = PythonRunner(port: 0)
        guard let url = runner.pidFileURL else {
            XCTFail("pidFileURL was nil")
            return
        }
        let caches = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first!
        XCTAssertTrue(url.path.hasPrefix(caches.path),
                      "pid file (\(url.path)) should be under Caches (\(caches.path))")
        XCTAssertEqual(url.lastPathComponent, "python.pid")
        XCTAssertTrue(url.path.contains("com.omeryasironal.claudewatch.tray"),
                      "pid file should be namespaced under the tray bundle id")
    }

    /// A PID file pointing at a process that doesn't exist must be cleaned
    /// up by reapOrphanIfAny() — otherwise we'd keep trying to SIGTERM a
    /// dead/recycled PID on every launch.
    func test_reapOrphanIfAny_removes_stale_pid_file() async throws {
        let runner = PythonRunner(port: 0)
        guard let url = runner.pidFileURL else {
            XCTFail("pidFileURL was nil")
            return
        }
        // PID 99999 is well above the default kernel max on macOS and is
        // overwhelmingly unlikely to be alive on a test host. If it does
        // happen to be alive, skip rather than risk SIGTERM-ing something
        // unrelated.
        let stalePid: Int32 = 99999
        if kill(stalePid, 0) == 0 {
            throw XCTSkip("PID \(stalePid) happens to be alive on this host; can't safely run this test.")
        }
        let iso = ISO8601DateFormatter().string(from: Date())
        try "\(stalePid) \(iso)\n".write(to: url, atomically: true, encoding: .utf8)
        XCTAssertTrue(FileManager.default.fileExists(atPath: url.path))

        await runner.reapOrphanIfAny()

        XCTAssertFalse(FileManager.default.fileExists(atPath: url.path),
                       "stale PID file should be removed after reapOrphanIfAny()")
    }

    /// Mirrors PythonRunner.locatePython()'s search list — best-effort.
    private static func bundledPythonExists() -> Bool {
        let candidates = [
            // .app bundle layout
            Bundle.main.resourceURL?
                .appendingPathComponent("python")
                .appendingPathComponent("bin")
                .appendingPathComponent("python3"),
            // Dev mode: repo-relative
            URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
                .appendingPathComponent("mac/build/python/bin/python3"),
            URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
                .appendingPathComponent("build/python/bin/python3"),
        ]
        for c in candidates {
            guard let c else { continue }
            if FileManager.default.isExecutableFile(atPath: c.path) {
                return true
            }
        }
        return false
    }
}
