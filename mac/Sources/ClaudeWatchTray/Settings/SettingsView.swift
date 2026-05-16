import SwiftUI

struct SettingsView: View {
    @StateObject var vm = SettingsViewModel()
    @EnvironmentObject var appVM: AppViewModel
    @ObservedObject private var notifMgr = NotificationManager.shared

    var body: some View {
        TabView {
            GeneralTab(vm: vm)
                .tabItem { Label("General", systemImage: "gearshape") }
            NotificationsTab(vm: vm, appVM: appVM, notifMgr: notifMgr)
                .tabItem { Label("Notifications", systemImage: "bell") }
            EditorTab(vm: vm)
                .tabItem { Label("Editor", systemImage: "doc.text") }
            RemoteControlTab(vm: vm)
                .tabItem { Label("Remote Control", systemImage: "paperplane") }
            AboutTab()
                .tabItem { Label("About", systemImage: "info.circle") }
        }
        .frame(width: 520, height: 440)
        .task {
            await vm.load()
            await notifMgr.refreshAuthorizationStatus()
        }
        .overlay(alignment: .bottom) {
            if let err = vm.lastError {
                Text(err)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .padding(8)
                    .background(.red.opacity(0.1))
                    .clipShape(RoundedRectangle(cornerRadius: 6))
                    .padding(8)
            } else if let saved = vm.lastSavedAt {
                Text("Saved \(saved.formatted(date: .omitted, time: .standard))")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .padding(4)
            }
        }
    }
}

// MARK: - General

private struct GeneralTab: View {
    @ObservedObject var vm: SettingsViewModel

    var body: some View {
        Form {
            Section("Plan") {
                Picker("Anthropic plan", selection: $vm.config.plan) {
                    Text(verbatim: "API (pay per token — show $)").tag("api")
                    Text(verbatim: "Pro ($20/mo flat — hide $)").tag("pro")
                    Text(verbatim: "Max 5× (hide $)").tag("max")
                    Text(verbatim: "Max 20× (hide $)").tag("max_20x")
                    Text(verbatim: "Team (hide $)").tag("team")
                    Text(verbatim: "Free (hide $)").tag("free")
                }
                .pickerStyle(.menu)
                .onChange(of: vm.config.plan) { Task { await vm.save() } }
                Text("For Max/Pro/Team plans, dollar costs are hidden everywhere since you pay a flat monthly fee. Token totals stay visible.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Section("Scheduler") {
                LabeledContent("Process scan interval") {
                    HStack {
                        Slider(value: $vm.config.processScanIntervalSeconds, in: 0.5...10, step: 0.5)
                        Text("\(String(format: "%.1f", vm.config.processScanIntervalSeconds))s")
                            .monospacedDigit()
                            .frame(width: 44, alignment: .trailing)
                    }
                }
                LabeledContent("iTerm refresh interval") {
                    HStack {
                        Slider(value: $vm.config.itermRefreshIntervalSeconds, in: 1...30, step: 1)
                        Text("\(Int(vm.config.itermRefreshIntervalSeconds))s")
                            .monospacedDigit()
                            .frame(width: 44, alignment: .trailing)
                    }
                }
            }
            Section("Privacy") {
                Toggle("Read-only mode (no focus/halt/new-session)", isOn: $vm.config.readOnly)
                Toggle("Privacy mode (redact log text in dashboard)", isOn: $vm.config.privacyMode)
                Toggle("Allow log text in dashboard (override privacy_mode for /log-tail)", isOn: $vm.config.showLogText)
                    .disabled(vm.config.privacyMode)
            }
            HStack {
                Spacer()
                Button("Save") { Task { await vm.save() } }
                    .keyboardShortcut(.return)
            }
        }
        .formStyle(.grouped)
        .padding(16)
    }
}

// MARK: - Notifications

private struct NotificationsTab: View {
    @ObservedObject var vm: SettingsViewModel
    @ObservedObject var appVM: AppViewModel
    @ObservedObject var notifMgr: NotificationManager

    var body: some View {
        Form {
            Section("Notification source") {
                Picker("Source", selection: $appVM.notificationSource) {
                    Text("Backend (osascript, basic)").tag(NotificationSource.backend)
                    Text("Native (this app, with Focus / Halt buttons)").tag(NotificationSource.native)
                }
                .pickerStyle(.radioGroup)

                if appVM.notificationSource == .native {
                    HStack(spacing: 6) {
                        Image(systemName: notifMgr.authorized
                              ? "checkmark.seal.fill" : "exclamationmark.triangle.fill")
                            .foregroundStyle(notifMgr.authorized ? .green : .orange)
                        Text(notifMgr.authorized
                             ? "macOS notification permission granted"
                             : "macOS hasn't authorized notifications yet")
                            .font(.caption)
                        Spacer()
                        if !notifMgr.authorized {
                            Button("Request") {
                                Task { await notifMgr.requestAuthorization() }
                            }
                            .controlSize(.small)
                        }
                    }
                    Text("Switching to Native disables the backend's osascript notifications so you don't get duplicates. Switch back to Backend to re-enable them.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else {
                    Text("Backend mode uses `osascript display notification` for compatibility. No action buttons; the dashboard or menu bar is the only path to Focus/Halt.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            Section("Master") {
                Toggle("Enable macOS notifications", isOn: $vm.config.notifications.enabled)
                if appVM.notificationSource == .native && vm.config.notifications.enabled {
                    Text("Note: in Native mode the backend's master switch is held off by the tray. The triggers below still apply to the native notifications.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            Section("Triggers") {
                Toggle("When a Claude session ends", isOn: $vm.config.notifications.onSessionEnd)
                Toggle("When a session crosses the cost threshold", isOn: $vm.config.notifications.onHighCost)
                LabeledContent("Cost threshold") {
                    HStack {
                        TextField("", value: $vm.config.notifications.costThresholdUsd,
                                  format: .currency(code: "USD"))
                            .frame(width: 110)
                            .multilineTextAlignment(.trailing)
                            .disabled(!vm.config.notifications.onHighCost)
                    }
                }
            }
            HStack {
                Spacer()
                Button("Save") { Task { await vm.save() } }
                    .keyboardShortcut(.return)
            }
        }
        .formStyle(.grouped)
        .padding(16)
    }
}

// MARK: - Editor

private struct EditorTab: View {
    @ObservedObject var vm: SettingsViewModel

    var body: some View {
        Form {
            Section("Open in editor") {
                Toggle("Enable \"Open in editor\" button", isOn: $vm.config.editor.enabled)
                TextField("Command", text: $vm.config.editor.command,
                          prompt: Text("code"))
                    .disabled(!vm.config.editor.enabled)
                Text("Typical values: `code` (VSCode CLI), `cursor`, `subl` (Sublime), `open -t` (default text editor). The command receives the absolute file path as a single argument.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            HStack {
                Spacer()
                Button("Save") { Task { await vm.save() } }
                    .keyboardShortcut(.return)
            }
        }
        .formStyle(.grouped)
        .padding(16)
    }
}

// MARK: - Remote Control

private struct RemoteControlTab: View {
    @ObservedObject var vm: SettingsViewModel

    var body: some View {
        Form {
            Section("Remote control") {
                Toggle("Allow sending messages to live Claude sessions", isOn: $vm.config.remoteControl.enabled)
                Text("When enabled, the dashboard chat panel forwards text to the corresponding iTerm session as if you typed it. Off by default. Off also blocks the POST /api/sessions/{pid}/send-text endpoint from accepting writes.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Section("Chat panel") {
                HStack(spacing: 6) {
                    Image(systemName: "bubble.left.and.bubble.right.fill")
                        .foregroundStyle(.tint)
                    Text("Click the chat icon on any session row to open a native chat window. The composer is read-only when remote control is disabled.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Text("Cmd+Return sends · Return inserts a newline (so multi-line prompts don't fire by accident).")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            HStack {
                Spacer()
                Button("Save") { Task { await vm.save() } }
                    .keyboardShortcut(.return)
            }
        }
        .formStyle(.grouped)
        .padding(16)
    }
}

// MARK: - About

private struct AboutTab: View {
    var version: String {
        Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "0.3.0"
    }
    var build: String {
        Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "1"
    }

    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "ant.fill")
                .font(.system(size: 56))
                .foregroundStyle(.tint)
            Text("ClaudeWatch").font(.system(.largeTitle, weight: .semibold))
            Text("Version \(version) (build \(build))")
                .font(.system(.body, design: .monospaced))
                .foregroundStyle(.secondary)
            Text("Local Claude Code session monitor for macOS")
                .font(.body)
                .foregroundStyle(.secondary)
            HStack(spacing: 12) {
                Link("GitHub", destination: URL(string: "https://github.com/OmerYasirOnal/claudewatch")!)
                Link("Issues", destination: URL(string: "https://github.com/OmerYasirOnal/claudewatch/issues")!)
            }
            Spacer()
            Text("MIT License · © 2026 Omer Yasir Onal")
                .font(.caption)
                .foregroundStyle(.tertiary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(24)
    }
}
