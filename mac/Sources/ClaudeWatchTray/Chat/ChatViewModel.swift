import Foundation
import OSLog

/// Renderable transcript entry. We collapse the JSONL log into a flat list
/// of role-tagged entries; tool blocks become small inline rows so the user
/// can see what Claude was doing without us re-implementing the whole
/// dashboard's tool-result viewer.
struct ChatEntry: Identifiable, Equatable {
    enum Role: String, Equatable {
        case user
        case assistant
        case toolUse
        case toolResult
        case system
        case redacted
    }

    let id: String
    let role: Role
    let text: String
    let toolName: String?
    let timestamp: Date?
}

/// Drives one chat window. Subscribes to `/api/sessions/{pid}/log-stream`,
/// parses entries by type, and exposes `send(text:)` that POSTs through
/// APIClient.
@MainActor
final class ChatViewModel: ObservableObject {
    @Published var entries: [ChatEntry] = []
    @Published var draft: String = ""
    @Published var lastError: String?
    @Published var sending: Bool = false
    @Published var remoteEnabled: Bool = false
    @Published var privacyRedacted: Bool = false
    @Published var connectionState: ConnectionState = .connecting

    enum ConnectionState: Equatable {
        case connecting
        case connected
        case disconnected
    }

    let pid: Int
    let projectName: String
    let model: String?

    private let api: APIClient
    private let port: Int
    private var streamTask: Task<Void, Never>?
    private var configRefreshTask: Task<Void, Never>?
    private let logger = Logger(subsystem: "com.omeryasironal.claudewatch.tray",
                                category: "ChatViewModel")

    init(session: Session, port: Int = APIClient.defaultPort) {
        self.pid = session.pid
        self.projectName = session.projectName
        self.model = session.model
        self.api = APIClient(port: port)
        self.port = port
    }

    deinit {
        streamTask?.cancel()
        configRefreshTask?.cancel()
    }

    func start() {
        loadConfig()
        startStream()
    }

    func stop() {
        streamTask?.cancel()
        streamTask = nil
        configRefreshTask?.cancel()
        configRefreshTask = nil
    }

    // MARK: - Config

    private func loadConfig() {
        configRefreshTask?.cancel()
        configRefreshTask = Task { @MainActor [weak self] in
            guard let self else { return }
            while !Task.isCancelled {
                if let cfg = try? await self.api.getConfig() {
                    self.remoteEnabled = cfg.remoteControl.enabled
                    self.privacyRedacted = !cfg.showLogText
                }
                // Refresh every 5s so flipping the toggle in Settings is
                // visible promptly without polling on every keystroke.
                try? await Task.sleep(for: .seconds(5))
            }
        }
    }

    // MARK: - Stream

    private func startStream() {
        streamTask?.cancel()
        connectionState = .connecting
        streamTask = Task { @MainActor [weak self] in
            await self?.runStreamLoop()
        }
    }

    private func runStreamLoop() async {
        var backoff: TimeInterval = 1
        while !Task.isCancelled {
            do {
                try await consume()
                backoff = 1
                connectionState = .disconnected
            } catch is CancellationError {
                return
            } catch {
                connectionState = .disconnected
                logger.debug("Chat stream error pid=\(self.pid): \(error.localizedDescription)")
                do { try await Task.sleep(for: .seconds(backoff)) } catch { return }
                backoff = min(backoff * 2, 30)
            }
        }
    }

    private func consume() async throws {
        guard let url = URL(string: "http://127.0.0.1:\(port)/api/sessions/\(pid)/log-stream") else {
            throw URLError(.badURL)
        }
        var req = URLRequest(url: url)
        req.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        req.timeoutInterval = .infinity

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
        connectionState = .connected

        var eventName: String?
        var buffer: String = ""
        for try await line in bytes.lines {
            if Task.isCancelled { return }
            if line.isEmpty {
                if !buffer.isEmpty {
                    dispatch(eventName: eventName ?? "message", data: buffer)
                }
                eventName = nil
                buffer = ""
                continue
            }
            if line.hasPrefix(":") { continue }
            if line.hasPrefix("event:") {
                eventName = String(line.dropFirst("event:".count))
                    .trimmingCharacters(in: .whitespaces)
            } else if line.hasPrefix("data:") {
                let chunk = String(line.dropFirst("data:".count))
                    .trimmingCharacters(in: .whitespaces)
                if !buffer.isEmpty { buffer.append("\n") }
                buffer.append(chunk)
            }
        }
    }

    private func dispatch(eventName: String, data: String) {
        guard let payload = data.data(using: .utf8) else { return }
        guard let obj = try? JSONSerialization.jsonObject(with: payload) as? [String: Any] else { return }

        switch eventName {
        case "snapshot":
            if let raw = obj["entries"] as? [[String: Any]] {
                entries = parseEntries(raw)
            }
        case "append":
            if let raw = obj["entries"] as? [[String: Any]] {
                entries.append(contentsOf: parseEntries(raw))
            }
        case "error":
            if let msg = obj["error"] as? String {
                lastError = msg
            }
        default:
            break
        }
    }

    private func parseEntries(_ raw: [[String: Any]]) -> [ChatEntry] {
        var out: [ChatEntry] = []
        for (idx, e) in raw.enumerated() {
            let type = (e["type"] as? String) ?? "unknown"
            let uuid = (e["uuid"] as? String) ?? UUID().uuidString
            let ts = parseTimestamp(e["timestamp"] as? String)
            let msg = e["message"] as? [String: Any]
            switch type {
            case "user":
                // Pre-redacted by the backend in privacy mode (text becomes [])
                let content = extractContent(msg)
                if content.text.isEmpty && privacyRedacted {
                    out.append(ChatEntry(id: "\(uuid)-\(idx)-redacted",
                                         role: .redacted,
                                         text: "[content hidden by privacy_mode]",
                                         toolName: nil,
                                         timestamp: ts))
                } else if !content.text.isEmpty {
                    out.append(ChatEntry(id: "\(uuid)-\(idx)-user",
                                         role: .user,
                                         text: content.text,
                                         toolName: nil,
                                         timestamp: ts))
                }
                // Tool results inside user messages — render as toolResult.
                for tr in content.toolResults {
                    out.append(ChatEntry(id: "\(uuid)-\(idx)-tr",
                                         role: .toolResult,
                                         text: tr,
                                         toolName: nil,
                                         timestamp: ts))
                }
            case "assistant":
                let content = extractContent(msg)
                if !content.text.isEmpty {
                    out.append(ChatEntry(id: "\(uuid)-\(idx)-asst",
                                         role: .assistant,
                                         text: content.text,
                                         toolName: nil,
                                         timestamp: ts))
                } else if privacyRedacted && content.toolUses.isEmpty {
                    out.append(ChatEntry(id: "\(uuid)-\(idx)-asst-redacted",
                                         role: .redacted,
                                         text: "[content hidden by privacy_mode]",
                                         toolName: nil,
                                         timestamp: ts))
                }
                for tool in content.toolUses {
                    out.append(ChatEntry(id: "\(uuid)-\(idx)-tu-\(tool)",
                                         role: .toolUse,
                                         text: "Used \(tool)",
                                         toolName: tool,
                                         timestamp: ts))
                }
            case "system":
                let content = extractContent(msg)
                if !content.text.isEmpty {
                    out.append(ChatEntry(id: "\(uuid)-\(idx)-sys",
                                         role: .system,
                                         text: content.text,
                                         toolName: nil,
                                         timestamp: ts))
                }
            default:
                break
            }
        }
        return out
    }

    private struct ContentBits {
        var text: String = ""
        var toolUses: [String] = []
        var toolResults: [String] = []
    }

    private func extractContent(_ msg: [String: Any]?) -> ContentBits {
        var bits = ContentBits()
        guard let msg else { return bits }
        // Sometimes message.content is a string (older entries), sometimes a list.
        if let s = msg["content"] as? String {
            bits.text = s
            return bits
        }
        guard let blocks = msg["content"] as? [[String: Any]] else { return bits }
        var pieces: [String] = []
        for block in blocks {
            let btype = block["type"] as? String
            switch btype {
            case "text":
                if let t = block["text"] as? String, !t.isEmpty {
                    pieces.append(t)
                }
            case "tool_use":
                if let n = block["name"] as? String {
                    bits.toolUses.append(n)
                }
            case "tool_result":
                // In privacy mode the backend already stripped the content array,
                // so this is purely informational.
                bits.toolResults.append("Tool result")
            default:
                break
            }
        }
        bits.text = pieces.joined(separator: "\n\n")
        return bits
    }

    private func parseTimestamp(_ raw: String?) -> Date? {
        guard let raw else { return nil }
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let d = iso.date(from: raw) { return d }
        iso.formatOptions = [.withInternetDateTime]
        return iso.date(from: raw)
    }

    // MARK: - Send

    /// POST the current draft to /api/sessions/{pid}/send-text. Disabled in
    /// the view layer when `remoteEnabled` is false; we re-check here as a
    /// belt-and-suspenders against stale UI state.
    func send() async {
        guard remoteEnabled else {
            lastError = "Remote control is disabled. Enable it in Settings → Remote Control."
            return
        }
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        sending = true
        defer { sending = false }
        do {
            try await api.sendText(pid: pid, text: text, submit: true)
            draft = ""
            lastError = nil
        } catch {
            lastError = "Send failed: \(error)"
        }
    }
}
