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
