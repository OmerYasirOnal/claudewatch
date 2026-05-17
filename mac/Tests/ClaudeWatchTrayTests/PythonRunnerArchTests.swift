import XCTest
import Foundation
@testable import ClaudeWatchTray

/// Exercises the arch-aware Python lookup helpers added for the universal-
/// binary build (UNIVERSAL=1). These tests are pure-FileManager — no
/// subprocesses, no port probes — so they're safe for any CI environment.
final class PythonRunnerArchTests: XCTestCase {

    private var tmp: URL!

    override func setUpWithError() throws {
        tmp = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("cw-pyrunner-arch-\(UUID().uuidString)",
                                    isDirectory: true)
        try FileManager.default.createDirectory(at: tmp,
                                                withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        if let tmp { try? FileManager.default.removeItem(at: tmp) }
    }

    /// Drop a fake `bin/python3` executable inside `<container>/<treeName>/`.
    @discardableResult
    private func makeFakePython(under container: URL, treeName: String) throws -> URL {
        let bin = container
            .appendingPathComponent(treeName)
            .appendingPathComponent("bin")
        try FileManager.default.createDirectory(at: bin,
                                                withIntermediateDirectories: true)
        let exe = bin.appendingPathComponent("python3")
        // Minimal script body — we never invoke it, just need the executable bit.
        try "#!/bin/sh\necho stub\n".write(to: exe, atomically: true,
                                            encoding: .utf8)
        try FileManager.default.setAttributes(
            [.posixPermissions: NSNumber(value: 0o755)],
            ofItemAtPath: exe.path)
        return exe
    }

    // MARK: - pythonCandidates ordering

    func test_pythonCandidates_prefersArchSpecificOverGeneric() {
        let cands = PythonRunner.pythonCandidates(under: tmp, archSuffix: "arm64")
        XCTAssertEqual(cands.count, 2)
        XCTAssertTrue(cands[0].path.hasSuffix("/python-arm64/bin/python3"),
                      "arch-specific candidate must come first; got \(cands[0].path)")
        XCTAssertTrue(cands[1].path.hasSuffix("/python/bin/python3"),
                      "generic candidate must come second; got \(cands[1].path)")
    }

    func test_pythonCandidates_x86_64Suffix() {
        let cands = PythonRunner.pythonCandidates(under: tmp, archSuffix: "x86_64")
        XCTAssertTrue(cands[0].path.hasSuffix("/python-x86_64/bin/python3"))
    }

    func test_pythonCandidates_emptySuffixYieldsGenericOnly() {
        let cands = PythonRunner.pythonCandidates(under: tmp, archSuffix: "")
        XCTAssertEqual(cands.count, 1)
        XCTAssertTrue(cands[0].path.hasSuffix("/python/bin/python3"))
    }

    // MARK: - firstExecutable

    func test_firstExecutable_returnsNilWhenNothingExists() {
        let cands = PythonRunner.pythonCandidates(under: tmp, archSuffix: "arm64")
        XCTAssertNil(PythonRunner.firstExecutable(in: cands))
    }

    func test_firstExecutable_picksArchSpecificWhenBothPresent() throws {
        try makeFakePython(under: tmp, treeName: "python-arm64")
        try makeFakePython(under: tmp, treeName: "python")
        let cands = PythonRunner.pythonCandidates(under: tmp, archSuffix: "arm64")
        let picked = try XCTUnwrap(PythonRunner.firstExecutable(in: cands))
        XCTAssertTrue(picked.path.hasSuffix("/python-arm64/bin/python3"),
                      "expected arch-specific tree to win; got \(picked.path)")
    }

    func test_firstExecutable_fallsBackToGenericWhenArchTreeMissing() throws {
        // Only the legacy single-tree layout exists — simulates a non-UNIVERSAL build.
        try makeFakePython(under: tmp, treeName: "python")
        let cands = PythonRunner.pythonCandidates(under: tmp, archSuffix: "arm64")
        let picked = try XCTUnwrap(PythonRunner.firstExecutable(in: cands))
        XCTAssertTrue(picked.path.hasSuffix("/python/bin/python3"),
                      "expected fallback to generic tree; got \(picked.path)")
    }

    func test_firstExecutable_ignoresArchTreeForOtherArch() throws {
        // Bundle ships python-x86_64 only; host claims arm64 → must fall back.
        try makeFakePython(under: tmp, treeName: "python-x86_64")
        try makeFakePython(under: tmp, treeName: "python")
        let cands = PythonRunner.pythonCandidates(under: tmp, archSuffix: "arm64")
        let picked = try XCTUnwrap(PythonRunner.firstExecutable(in: cands))
        XCTAssertTrue(picked.path.hasSuffix("/python/bin/python3"),
                      "should skip python-x86_64 when arch is arm64; got \(picked.path)")
    }

    // MARK: - hostArchSuffix

    func test_hostArchSuffix_isOneOfTheKnownValues() {
        let s = PythonRunner.hostArchSuffix
        // Compile-time arch detection — should always resolve to one of these
        // on any mac/linux runner we'd ever build on.
        XCTAssertTrue(s == "arm64" || s == "x86_64" || s == "",
                      "unexpected hostArchSuffix: \(s)")
    }
}
