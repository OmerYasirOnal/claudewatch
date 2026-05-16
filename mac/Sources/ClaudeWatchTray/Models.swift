import Foundation

/// Matches `backend/models.py::ClaudeSession` — only the fields the tray uses.
/// Unknown fields are ignored by JSONDecoder by default.
struct Session: Decodable, Identifiable {
    let pid: Int
    let cwd: String
    let startedAt: Date
    let durationSeconds: Int
    let status: String                  // "working" | "waiting" | "idle"
    let locationType: String            // "iterm" | "tmux" | "headless"
    let itermSessionId: String?
    let itermTabTitle: String?
    let model: String?
    let messageCount: Int
    let usage: Usage?
    let currentTaskSubject: String?
    let isInFlight: Bool

    var id: Int { pid }
    var projectName: String { (cwd as NSString).lastPathComponent }

    var costEstimate: Double { usage?.costEstimateUsd ?? 0 }
    var totalTokens: Int { usage?.totalTokens ?? 0 }

    enum CodingKeys: String, CodingKey {
        case pid, cwd, status, model, usage
        case startedAt = "started_at"
        case durationSeconds = "duration_seconds"
        case locationType = "location_type"
        case itermSessionId = "iterm_session_id"
        case itermTabTitle = "iterm_tab_title"
        case messageCount = "message_count"
        case currentTaskSubject = "current_task_subject"
        case isInFlight = "is_in_flight"
    }
}

struct Usage: Decodable {
    let totalTokens: Int
    let costEstimateUsd: Double?

    enum CodingKeys: String, CodingKey {
        case totalTokens = "total_tokens"
        case costEstimateUsd = "cost_estimate_usd"
    }
}

struct HealthReport: Decodable {
    let itermApi: Bool
    let tmuxAvailable: Bool
    let logDirFound: Bool
    let issues: [String]

    enum CodingKeys: String, CodingKey {
        case itermApi = "iterm_api"
        case tmuxAvailable = "tmux_available"
        case logDirFound = "log_dir_found"
        case issues
    }
}
