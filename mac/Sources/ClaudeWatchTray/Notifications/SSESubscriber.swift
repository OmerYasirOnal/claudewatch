import Foundation
import OSLog

/// Long-lived Server-Sent Events consumer for the backend's `/api/stream`
/// endpoint. Reconnects with capped exponential backoff (1s → 30s) and parses
/// `event:` + `data:` blocks into typed events.
///
/// Owned by AppViewModel; started when the user picks the "Native"
/// notification source and cancelled when they switch back.
@MainActor
final class SSESubscriber {
    /// Lightweight cache of last-seen sessions, indexed by PID, so we can
    /// resolve a project name for `session.ended` events (which only ship
    /// the pid). Mutated only from the main actor; the parser dispatches
    /// onto the actor before reading.
    private var sessionsByPid: [Int: Session] = [:]

    private var task: Task<Void, Never>?
    private let baseURL: URL
    private let logger = Logger(subsystem: "com.omeryasironal.claudewatch.tray",
                                category: "SSESubscriber")

    /// Called for every parsed event. The closure runs on the main actor.
    var onEvent: ((SSEEvent) -> Void)?

    init(port: Int = APIClient.defaultPort) {
        self.baseURL = URL(string: "http://127.0.0.1:\(port)")!
    }

    /// Start consuming. Idempotent — calling twice is a no-op.
    func start() {
        guard task == nil else { return }
        task = Task { [weak self] in
            await self?.runLoop()
        }
    }

    func stop() {
        task?.cancel()
        task = nil
    }

    // MARK: - Connection loop

    private func runLoop() async {
        var backoff: TimeInterval = 1
        while !Task.isCancelled {
            do {
                try await consume()
                // Clean disconnect (server closed). Reset backoff and retry.
                backoff = 1
            } catch is CancellationError {
                return
            } catch {
                logger.debug("SSE error: \(error.localizedDescription). Reconnecting in \(Int(backoff))s")
                do {
                    try await Task.sleep(for: .seconds(backoff))
                } catch {
                    return
                }
                backoff = min(backoff * 2, 30)
            }
        }
    }

    private func consume() async throws {
        var req = URLRequest(url: baseURL.appendingPathComponent("/api/stream"))
        req.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        // SSE streams are long-lived; don't time out the resource.
        req.timeoutInterval = .infinity

        // Dedicated session so the global URLSession's 5s timeout in APIClient
        // doesn't fight the long-lived stream.
        let cfg = URLSessionConfiguration.default
        cfg.timeoutIntervalForRequest = 60
        cfg.timeoutIntervalForResource = .infinity
        cfg.httpCookieAcceptPolicy = .never
        let session = URLSession(configuration: cfg)
        defer { session.invalidateAndCancel() }

        let (bytes, response) = try await session.bytes(for: req)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw URLError(.badServerResponse)
        }

        var eventName: String?
        var dataBuffer: String = ""
        for try await line in bytes.lines {
            if Task.isCancelled { return }
            if line.isEmpty {
                // Dispatch the buffered event.
                if !dataBuffer.isEmpty {
                    let name = eventName ?? "message"
                    let payload = dataBuffer
                    dispatch(eventName: name, data: payload)
                }
                eventName = nil
                dataBuffer = ""
                continue
            }
            if line.hasPrefix(":") {
                // Comment / keepalive — ignore.
                continue
            }
            if line.hasPrefix("event:") {
                eventName = String(line.dropFirst("event:".count))
                    .trimmingCharacters(in: .whitespaces)
            } else if line.hasPrefix("data:") {
                let chunk = String(line.dropFirst("data:".count))
                    .trimmingCharacters(in: .whitespaces)
                if !dataBuffer.isEmpty { dataBuffer.append("\n") }
                dataBuffer.append(chunk)
            }
            // Other SSE fields (id:, retry:) — we don't use them.
        }
    }

    // MARK: - Dispatch

    private func dispatch(eventName: String, data: String) {
        guard let json = data.data(using: .utf8) else { return }
        guard let obj = try? JSONSerialization.jsonObject(with: json) as? [String: Any] else { return }

        switch eventName {
        case "snapshot":
            // Initial state — list of sessions at connect time.
            if let arr = obj["sessions"] as? [[String: Any]] {
                cacheSessions(arr)
            }
            onEvent?(.snapshot)
        case "session.started", "session.updated":
            if let sessDict = obj["session"] as? [String: Any],
               let sess = decodeSession(sessDict) {
                sessionsByPid[sess.pid] = sess
                if eventName == "session.started" {
                    onEvent?(.sessionStarted(sess))
                } else {
                    onEvent?(.sessionUpdated(sess))
                }
            }
        case "session.ended":
            let pid: Int?
            if let n = obj["pid"] as? Int { pid = n }
            else if let n = obj["pid"] as? NSNumber { pid = n.intValue }
            else { pid = nil }
            guard let pid else { return }
            let name = sessionsByPid[pid]?.projectName
            sessionsByPid.removeValue(forKey: pid)
            onEvent?(.sessionEnded(pid: pid, projectName: name))
        default:
            break
        }
    }

    private func cacheSessions(_ raw: [[String: Any]]) {
        for dict in raw {
            if let sess = decodeSession(dict) {
                sessionsByPid[sess.pid] = sess
            }
        }
    }

    private func decodeSession(_ dict: [String: Any]) -> Session? {
        // Re-serialize and run through Decodable so we don't drift from the
        // model's own key mapping. Date parsing matches APIClient's strategy.
        guard let data = try? JSONSerialization.data(withJSONObject: dict) else { return nil }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let raw = try container.decode(String.self)
            let iso = ISO8601DateFormatter()
            iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            if let d = iso.date(from: raw) { return d }
            iso.formatOptions = [.withInternetDateTime]
            if let d = iso.date(from: raw) { return d }
            throw DecodingError.dataCorruptedError(in: container, debugDescription: "bad date \(raw)")
        }
        return try? decoder.decode(Session.self, from: data)
    }
}

/// Subset of backend SSE events the tray cares about.
enum SSEEvent {
    case snapshot
    case sessionStarted(Session)
    case sessionUpdated(Session)
    case sessionEnded(pid: Int, projectName: String?)
}
