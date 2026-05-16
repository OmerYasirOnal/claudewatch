import Foundation
import OSLog

/// Spawns and supervises the bundled Python backend (uvicorn + claudewatch
/// FastAPI app). Looks for the runtime inside the .app's Resources/ first;
/// falls back to a developer-mode path so `swift run` from the repo also works.
///
/// State machine:
///   .idle → .checking → (port already in use) → .external
///                     → (no one listening)    → .launching → .running
///                                                          → .failed(reason)
@MainActor
final class PythonRunner: ObservableObject {
    enum State: Equatable {
        case idle
        case checking
        case launching
        case running(pid: Int32)
        /// Backend is listening but we didn't launch it (another claudewatch
        /// process is owning the port — e.g. user has `claudewatch start --daemon`
        /// from a previous install). We won't fight it; we just observe.
        case external
        case failed(String)
    }

    @Published private(set) var state: State = .idle
    @Published private(set) var lastLogLine: String = ""

    private var process: Process?
    private var pipe: Pipe?
    private let port: Int
    private let logger = Logger(subsystem: "com.omeryasironal.claudewatch.tray", category: "PythonRunner")

    init(port: Int = 7788) {
        self.port = port
    }

    /// Entry point: check if the port is taken; if not, launch our bundled
    /// Python. Idempotent.
    func startIfNeeded() async {
        guard case .idle = state else { return }
        state = .checking
        if await isBackendUp() {
            logger.info("Backend already responsive on :\(self.port); will observe externally")
            state = .external
            return
        }
        do {
            try launchBundledBackend()
        } catch {
            logger.error("Failed to launch bundled backend: \(error.localizedDescription)")
            state = .failed(error.localizedDescription)
            return
        }
        // Wait for /api/health to come up (max 15s).
        let deadline = Date().addingTimeInterval(15)
        while Date() < deadline {
            if await isBackendUp() {
                if let pid = process?.processIdentifier {
                    logger.info("Bundled backend ready, PID \(pid)")
                    state = .running(pid: pid)
                } else {
                    state = .running(pid: -1)
                }
                return
            }
            try? await Task.sleep(for: .milliseconds(300))
        }
        state = .failed("Backend did not respond on :\(port) within 15s")
        process?.terminate()
        process = nil
    }

    /// Best-effort graceful shutdown. SIGTERM, wait up to 3s, then SIGKILL.
    func stop() {
        guard let proc = process, proc.isRunning else {
            process = nil
            state = .idle
            return
        }
        proc.terminate()
        // Wait briefly for graceful exit
        let deadline = Date().addingTimeInterval(3)
        while proc.isRunning && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.1)
        }
        if proc.isRunning {
            kill(proc.processIdentifier, SIGKILL)
        }
        process = nil
        state = .idle
    }

    // MARK: - private

    private func launchBundledBackend() throws {
        let (python, repoRoot) = try locatePython()
        let backendModule = "backend.server:app"
        let args = [
            "-m", "uvicorn",
            backendModule,
            "--host", "127.0.0.1",
            "--port", String(port),
            "--log-level", "info",
            "--timeout-graceful-shutdown", "3",
        ]
        let proc = Process()
        proc.executableURL = python
        proc.arguments = args
        // Working directory = the directory containing the `backend/` package.
        proc.currentDirectoryURL = repoRoot

        // Capture stderr/stdout to surface in the UI.
        let pipe = Pipe()
        proc.standardOutput = pipe
        proc.standardError = pipe
        self.pipe = pipe

        // Drain pipe on a background queue and push the latest line to the UI.
        pipe.fileHandleForReading.readabilityHandler = { [weak self] fh in
            let data = fh.availableData
            guard !data.isEmpty, let line = String(data: data, encoding: .utf8) else { return }
            let trimmed = line.split(separator: "\n").last.map(String.init) ?? line
            Task { @MainActor in
                self?.lastLogLine = trimmed.trimmingCharacters(in: .whitespacesAndNewlines)
            }
        }

        // Ensure the child is not orphaned if we crash.
        var env = ProcessInfo.processInfo.environment
        // The bundled python ships its own stdlib; clear PYTHONHOME/PYTHONPATH
        // so we don't import from a system Python by accident.
        env.removeValue(forKey: "PYTHONHOME")
        env.removeValue(forKey: "PYTHONPATH")
        env["PYTHONUNBUFFERED"] = "1"
        // Point the backend at the bundle's copy of the dashboard.
        let frontend = repoRoot.appendingPathComponent("frontend")
        if FileManager.default.fileExists(atPath: frontend.path) {
            env["CLAUDEWATCH_FRONTEND_DIR"] = frontend.path
        }
        proc.environment = env

        try proc.run()
        self.process = proc
        state = .launching

        // Set up a termination handler so we surface unexpected exits.
        proc.terminationHandler = { [weak self] p in
            Task { @MainActor in
                guard let self else { return }
                if case .running = self.state {
                    self.state = .failed("Backend exited unexpectedly (code \(p.terminationStatus))")
                }
                self.process = nil
            }
        }
    }

    /// Find the bundled Python interpreter. Two layouts supported:
    /// 1. Inside the .app bundle: Contents/Resources/python/bin/python3
    /// 2. Developer mode (swift run from repo): mac/build/python/bin/python3
    private func locatePython() throws -> (URL, URL) {
        // Bundled
        if let resources = Bundle.main.resourceURL {
            let bundled = resources
                .appendingPathComponent("python")
                .appendingPathComponent("bin")
                .appendingPathComponent("python3")
            if FileManager.default.isExecutableFile(atPath: bundled.path) {
                // Repo root for the .app = Resources/, since backend lives in site-packages.
                return (bundled, resources)
            }
        }

        // Dev mode: walk up from the running binary to find mac/build/python
        let exe = Bundle.main.executableURL ?? URL(fileURLWithPath: CommandLine.arguments[0])
        let candidates = [
            exe.deletingLastPathComponent()
                .appendingPathComponent("../../../mac/build/python/bin/python3")
                .standardizedFileURL,
            exe.deletingLastPathComponent()
                .appendingPathComponent("../../mac/build/python/bin/python3")
                .standardizedFileURL,
            URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
                .appendingPathComponent("mac/build/python/bin/python3"),
        ]
        for cand in candidates {
            if FileManager.default.isExecutableFile(atPath: cand.path) {
                // Dev mode: backend module lives in the parent of mac/ (the repo root).
                let repoRoot = cand
                    .deletingLastPathComponent()                     // bin
                    .deletingLastPathComponent()                     // python
                    .deletingLastPathComponent()                     // build
                    .deletingLastPathComponent()                     // mac
                return (cand, repoRoot)
            }
        }
        throw NSError(domain: "PythonRunner", code: 1, userInfo: [
            NSLocalizedDescriptionKey: "Bundled Python not found. Did you run `make download-python && make bundle-backend`?",
        ])
    }

    private func isBackendUp() async -> Bool {
        guard let url = URL(string: "http://127.0.0.1:\(port)/api/health") else { return false }
        var req = URLRequest(url: url)
        req.timeoutInterval = 1
        do {
            let (_, response) = try await URLSession.shared.data(for: req)
            return (response as? HTTPURLResponse)?.statusCode == 200
        } catch {
            return false
        }
    }
}
