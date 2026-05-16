import Foundation

/// Subset of the backend's TOML config that we expose in the native Settings UI.
/// Decoded leniently — unknown keys ignored, missing keys default.
struct AppConfig: Codable, Equatable {
    var port: Int = 7788
    var plan: String = "api"            // "api" | "pro" | "max" | "max_20x" | "team" | "free"
    var readOnly: Bool = false
    var privacyMode: Bool = true
    var showLogText: Bool = false
    var processScanIntervalSeconds: Double = 2
    var itermRefreshIntervalSeconds: Double = 5
    var notifications: NotificationConfig = .init()
    var remoteControl: RemoteControlConfig = .init()
    var editor: EditorConfig = .init()

    enum CodingKeys: String, CodingKey {
        case port, plan, notifications, editor
        case readOnly = "read_only"
        case privacyMode = "privacy_mode"
        case showLogText = "show_log_text"
        case processScanIntervalSeconds = "process_scan_interval_seconds"
        case itermRefreshIntervalSeconds = "iterm_refresh_interval_seconds"
        case remoteControl = "remote_control"
    }

    // Tolerant decoder: missing fields keep struct defaults instead of throwing.
    init(from decoder: Decoder) throws {
        var cfg = AppConfig()
        let c = try decoder.container(keyedBy: CodingKeys.self)
        cfg.port = (try? c.decode(Int.self, forKey: .port)) ?? cfg.port
        cfg.plan = (try? c.decode(String.self, forKey: .plan)) ?? cfg.plan
        cfg.readOnly = (try? c.decode(Bool.self, forKey: .readOnly)) ?? cfg.readOnly
        cfg.privacyMode = (try? c.decode(Bool.self, forKey: .privacyMode)) ?? cfg.privacyMode
        cfg.showLogText = (try? c.decode(Bool.self, forKey: .showLogText)) ?? cfg.showLogText
        cfg.processScanIntervalSeconds = (try? c.decode(Double.self, forKey: .processScanIntervalSeconds)) ?? cfg.processScanIntervalSeconds
        cfg.itermRefreshIntervalSeconds = (try? c.decode(Double.self, forKey: .itermRefreshIntervalSeconds)) ?? cfg.itermRefreshIntervalSeconds
        cfg.notifications = (try? c.decode(NotificationConfig.self, forKey: .notifications)) ?? cfg.notifications
        cfg.remoteControl = (try? c.decode(RemoteControlConfig.self, forKey: .remoteControl)) ?? cfg.remoteControl
        cfg.editor = (try? c.decode(EditorConfig.self, forKey: .editor)) ?? cfg.editor
        self = cfg
    }

    init() {}
}

struct NotificationConfig: Codable, Equatable {
    var enabled: Bool = true
    var onSessionEnd: Bool = true
    var onHighCost: Bool = true
    var costThresholdUsd: Double = 5.0

    enum CodingKeys: String, CodingKey {
        case enabled
        case onSessionEnd = "on_session_end"
        case onHighCost = "on_high_cost"
        case costThresholdUsd = "cost_threshold_usd"
    }

    init() {}
    init(from decoder: Decoder) throws {
        var c = NotificationConfig()
        let k = try decoder.container(keyedBy: CodingKeys.self)
        c.enabled = (try? k.decode(Bool.self, forKey: .enabled)) ?? c.enabled
        c.onSessionEnd = (try? k.decode(Bool.self, forKey: .onSessionEnd)) ?? c.onSessionEnd
        c.onHighCost = (try? k.decode(Bool.self, forKey: .onHighCost)) ?? c.onHighCost
        c.costThresholdUsd = (try? k.decode(Double.self, forKey: .costThresholdUsd)) ?? c.costThresholdUsd
        self = c
    }
}

struct RemoteControlConfig: Codable, Equatable {
    var enabled: Bool = false
    init() {}
    init(from decoder: Decoder) throws {
        var c = RemoteControlConfig()
        let k = try decoder.container(keyedBy: CodingKeys.self)
        c.enabled = (try? k.decode(Bool.self, forKey: .enabled)) ?? c.enabled
        self = c
    }
}

struct EditorConfig: Codable, Equatable {
    var enabled: Bool = false
    var command: String = "code"
    init() {}
    init(from decoder: Decoder) throws {
        var c = EditorConfig()
        let k = try decoder.container(keyedBy: CodingKeys.self)
        c.enabled = (try? k.decode(Bool.self, forKey: .enabled)) ?? c.enabled
        c.command = (try? k.decode(String.self, forKey: .command)) ?? c.command
        self = c
    }
}
