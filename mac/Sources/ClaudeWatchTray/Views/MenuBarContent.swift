import SwiftUI

struct MenuBarContent: View {
    @ObservedObject var vm: AppViewModel
    @ObservedObject var runner: PythonRunner

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
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 4)
        .background(color.opacity(0.06))
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
                            onHalt: { vm.halt(sess.pid) }
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
    /// etc.). Kept separate from the main action bar so quit/refresh/settings
    /// stay visually prominent.
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
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
    }
}
