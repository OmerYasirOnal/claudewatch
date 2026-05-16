import AppKit
import Foundation
import SwiftUI

/// State + polling for the menu bar app.
@MainActor
final class AppViewModel: ObservableObject {
    @Published var sessions: [Session] = []
    @Published var health: HealthReport?
    @Published var lastError: String?
    @Published var lastUpdated: Date?

    private let api = APIClient()
    private var pollTask: Task<Void, Never>?

    var activeCount: Int { sessions.count }
    var totalCost: Double {
        sessions.map(\.costEstimate).reduce(0, +)
    }
    var menuBarLabel: String {
        if activeCount == 0 { return "" }
        if totalCost > 0 {
            return "\(activeCount) · $\(String(format: "%.2f", totalCost))"
        }
        return "\(activeCount)"
    }

    init() {
        start()
    }

    deinit {
        pollTask?.cancel()
    }

    func start() {
        pollTask?.cancel()
        pollTask = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                await self?.refresh()
                try? await Task.sleep(for: .seconds(3))
            }
        }
    }

    func refresh() async {
        do {
            sessions = try await api.listSessions()
                .sorted { ($0.costEstimate, $0.pid) > ($1.costEstimate, $1.pid) }
            lastError = nil
            lastUpdated = Date()
            // Health is cheaper than sessions; refresh occasionally.
            if Int.random(in: 0..<5) == 0 {
                if let h = try? await api.health() { health = h }
            }
        } catch APIError.transport {
            lastError = "Backend unreachable. Run: claudewatch start --daemon"
            sessions = []
        } catch {
            lastError = "API error: \(error)"
        }
    }

    func focus(_ pid: Int) {
        Task {
            do { try await api.focus(pid) }
            catch { lastError = "Focus failed: \(error)" }
        }
    }

    func halt(_ pid: Int) {
        Task {
            do { try await api.halt(pid) }
            catch { lastError = "Halt failed: \(error)" }
        }
    }

    func openDashboard() {
        if let url = URL(string: "http://127.0.0.1:7788/") {
            NSWorkspace.shared.open(url)
        }
    }

    func quit() {
        NSApplication.shared.terminate(nil)
    }
}
