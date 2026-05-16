import SwiftUI

struct MenuBarContent: View {
    @ObservedObject var vm: AppViewModel
    @ObservedObject var runner: PythonRunner
    @ObservedObject private var notifMgr = NotificationManager.shared

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            backendStatusBar
            Divider()
            sessionList
            Divider()
            footer
            Divider()
            metaFooter
        }
        .frame(width: 380)
        .task {
            // Kick off bundled-backend startup the first time the popover opens.
            // Idempotent — won't re-launch if already running or external.
            await runner.startIfNeeded()
        }
    }

    @ViewBuilder
    private var backendStatusBar: some View {
        switch runner.state {
        case .idle, .checking:
            statusRow(color: .gray, icon: "circle.dotted", text: "Connecting to backend…")
        case .launching:
            statusRow(color: .yellow, icon: "bolt", text: "Starting bundled backend…")
        case .running(let pid):
            statusRow(color: .green, icon: "checkmark.circle.fill",
                      text: "Backend running (PID \(pid))")
        case .external:
            statusRow(color: .blue, icon: "link",
                      text: "Backend already running (external daemon)")
        case .failed(let msg):
            statusRow(color: .red, icon: "exclamationmark.triangle.fill",
                      text: "Backend failed: \(msg)")
        }
    }

    private func statusRow(color: Color, icon: String, text: String) -> some View {
        HStack(spacing: 6) {
            Image(systemName: icon).foregroundStyle(color)
            Text(text)
                .font(.caption)
                .lineLimit(2)
                .foregroundStyle(.secondary)
            Spacer()
            // Surface the active notification source so the user always knows
            // who's responsible for the next banner that pops.
            notificationSourceBadge
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 4)
        .background(color.opacity(0.06))
    }

    /// Tiny badge on the right of the backend status row indicating whether
    /// notifications are coming from the tray (native) or backend (osascript).
    /// Tinted bell + "Native" pill when native, hollow bell otherwise. Hidden
    /// entirely when notifications are paused so the user notices the silence.
    @ViewBuilder
    private var notificationSourceBadge: some View {
        if let _ = notifMgr.pausedUntil {
            Label("Paused", systemImage: "bell.slash.fill")
                .labelStyle(.iconOnly)
                .foregroundStyle(.orange)
                .help("Notifications paused — click Resume in the footer")
        } else if vm.notificationSource == .native {
            HStack(spacing: 3) {
                Image(systemName: "bell.badge.fill")
                Text("Native")
            }
            .font(.caption2)
            .padding(.horizontal, 5)
            .padding(.vertical, 1)
            .background(Capsule().fill(Color.accentColor.opacity(0.18)))
            .foregroundStyle(.tint)
            .help("Tray-side actionable notifications are active")
        } else {
            Image(systemName: "bell")
                .font(.caption2)
                .foregroundStyle(.secondary)
                .help("Backend (osascript) notifications")
        }
    }

    private var header: some View {
        HStack(spacing: 8) {
            Image(systemName: "ant.fill")
                .foregroundStyle(.tint)
            Text("ClaudeWatch")
                .font(.system(.headline))
            Spacer()
            VStack(alignment: .trailing, spacing: 0) {
                Text("\(vm.activeCount) active")
                    .font(.system(.caption, design: .monospaced))
                if vm.totalCost > 0 {
                    Text("$\(String(format: "%.2f", vm.totalCost))")
                        .font(.system(.caption, design: .monospaced))
                        .foregroundStyle(.secondary)
                }
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
    }

    @ViewBuilder
    private var sessionList: some View {
        if let err = vm.lastError {
            errorPlaceholder(err)
        } else if vm.sessions.isEmpty {
            emptyPlaceholder
        } else {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0) {
                    ForEach(vm.sessions) { sess in
                        SessionRow(
                            sess: sess,
                            onFocus: { vm.focus(sess.pid) },
                            onHalt: { vm.halt(sess.pid) },
                            onChat: { vm.openChat(for: sess) }
                        )
                        Divider()
                    }
                }
            }
            .frame(maxHeight: 480)
        }
    }

    private var emptyPlaceholder: some View {
        VStack(spacing: 6) {
            Image(systemName: "moon.zzz")
                .font(.title2)
                .foregroundStyle(.secondary)
            Text("No active Claude sessions")
                .font(.system(.body))
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 24)
    }

    private func errorPlaceholder(_ msg: String) -> some View {
        VStack(spacing: 6) {
            Image(systemName: "exclamationmark.triangle")
                .font(.title2)
                .foregroundStyle(.orange)
            Text(msg)
                .font(.system(.caption))
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding(16)
    }

    private var footer: some View {
        HStack(spacing: 8) {
            Button {
                vm.openDashboard()
            } label: {
                Label("Open Dashboard", systemImage: "safari")
            }
            .buttonStyle(.bordered)
            .controlSize(.small)

            Spacer()

            Button {
                vm.openSettings()
            } label: {
                Image(systemName: "gearshape")
            }
            .help("Settings (⌘,)")
            .buttonStyle(.bordered)
            .controlSize(.small)

            Button("Refresh") {
                Task { await vm.refresh() }
            }
            .buttonStyle(.bordered)
            .controlSize(.small)

            Button(role: .destructive) {
                vm.quit()
            } label: {
                Image(systemName: "power")
            }
            .help("Quit")
            .buttonStyle(.bordered)
            .controlSize(.small)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
    }

    /// Secondary footer with low-noise affordances (re-open the welcome flow,
    /// pause notifications, etc.). Kept separate from the main action bar so
    /// quit/refresh/settings stay visually prominent.
    private var metaFooter: some View {
        HStack(spacing: 8) {
            Button {
                WelcomeController.shared.reset()
                WelcomeController.shared.show()
            } label: {
                Label("Show welcome again", systemImage: "sparkles")
                    .font(.caption)
            }
            .buttonStyle(.plain)
            .foregroundStyle(.secondary)
            .help("Re-run the first-launch welcome flow")

            Spacer()

            pauseMenu
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
    }

    /// Quick-action menu for pausing notifications. Always present so the user
    /// can mute even when the backend is the source — the pause flag lives on
    /// NotificationManager which gates `handleSessionUpdated/Ended`. Backend
    /// osascripts aren't affected by this flag; toggling the master switch in
    /// Settings does that. We document the distinction in the menu copy.
    @ViewBuilder
    private var pauseMenu: some View {
        if let until = notifMgr.pausedUntil {
            Button {
                notifMgr.resume()
            } label: {
                Label(pauseLabel(until: until), systemImage: "bell.slash.fill")
                    .font(.caption)
            }
            .buttonStyle(.plain)
            .foregroundStyle(.orange)
            .help("Resume native notifications")
        } else {
            Menu {
                Button("Pause for 1 hour") {
                    notifMgr.pause(for: 60 * 60)
                }
                Button("Pause until next launch") {
                    notifMgr.pauseUntilNextLaunch()
                }
            } label: {
                Label("Pause notifications", systemImage: "bell.slash")
                    .font(.caption)
            }
            .menuStyle(.borderlessButton)
            .menuIndicator(.hidden)
            .fixedSize()
            .foregroundStyle(.secondary)
            .help("Mute tray-side notifications temporarily")
        }
    }

    private func pauseLabel(until: Date) -> String {
        if until == .distantFuture { return "Paused until next launch" }
        let f = RelativeDateTimeFormatter()
        f.unitsStyle = .abbreviated
        return "Paused \(f.localizedString(for: until, relativeTo: Date()))"
    }
}
