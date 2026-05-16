import SwiftUI

struct MenuBarContent: View {
    @ObservedObject var vm: AppViewModel

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider()
            sessionList
            Divider()
            footer
        }
        .frame(width: 380)
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
            .buttonStyle(.bordered)
            .controlSize(.small)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
    }
}
