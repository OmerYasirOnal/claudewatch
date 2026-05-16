import AppKit
import SwiftUI
import UserNotifications

/// 4-panel onboarding flow shown on first launch. Each panel exposes a
/// "Continue" CTA that advances the TabView selection; the final panel calls
/// `onFinish` which persists the welcomeShown flag and closes the window.
struct WelcomeView: View {
    let onFinish: () -> Void
    @State private var panel: Int = 0
    @State private var notificationStatus: NotificationStatus = .unknown

    var body: some View {
        ZStack {
            // Soft brand-coloured backdrop reusing the icon's gradient.
            LinearGradient(
                colors: [
                    Color(red: 0.18, green: 0.78, blue: 0.55).opacity(0.18),
                    Color(red: 0.07, green: 0.49, blue: 0.74).opacity(0.18),
                ],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
            .ignoresSafeArea()

            VStack(spacing: 0) {
                TabView(selection: $panel) {
                    welcomePanel.tag(0)
                    automationPanel.tag(1)
                    notificationsPanel.tag(2)
                    donePanel.tag(3)
                }
                .tabViewStyle(.automatic)
                .frame(maxWidth: .infinity, maxHeight: .infinity)

                pageIndicator
                    .padding(.bottom, 16)
            }
            .padding(.top, 12)
        }
        .frame(width: 560, height: 420)
    }

    // MARK: - Panel 1: Welcome ------------------------------------------------

    private var welcomePanel: some View {
        VStack(spacing: 18) {
            iconBadge

            Text("Welcome to ClaudeWatch")
                .font(.system(size: 24, weight: .semibold))

            Text("A tiny menu-bar app that watches your local **claude** CLI sessions — costs, tokens, current step, and a few one-click actions (focus, halt, open in editor).")
                .font(.system(size: 13))
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
                .frame(maxWidth: 420)
                .fixedSize(horizontal: false, vertical: true)

            Spacer()

            primaryButton("Continue") { panel = 1 }
        }
        .padding(24)
    }

    // MARK: - Panel 2: iTerm Automation --------------------------------------

    private var automationPanel: some View {
        VStack(spacing: 16) {
            sectionHeader(icon: "terminal", title: "iTerm Automation")

            Text("ClaudeWatch uses AppleScript to focus or send keystrokes to your iTerm sessions (Focus, Halt, Send Text). macOS treats this as automation control and asks for permission the first time it happens.")
                .font(.system(size: 12.5))
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
                .frame(maxWidth: 460)
                .fixedSize(horizontal: false, vertical: true)

            VStack(spacing: 6) {
                Text("System Settings → Privacy & Security → Automation")
                    .font(.system(size: 12, weight: .medium, design: .monospaced))
                Text("Allow ClaudeWatch → iTerm")
                    .font(.system(size: 11.5, design: .monospaced))
                    .foregroundStyle(.secondary)
            }
            .padding(10)
            .frame(maxWidth: .infinity)
            .background(
                RoundedRectangle(cornerRadius: 8)
                    .fill(Color.primary.opacity(0.05))
            )
            .padding(.horizontal, 28)

            Button {
                openAutomationSettings()
            } label: {
                Label("Open Privacy & Security Settings", systemImage: "arrow.up.right.square")
            }
            .controlSize(.regular)

            Text("You can skip this for now — macOS will prompt again the first time ClaudeWatch tries to control iTerm.")
                .font(.system(size: 11))
                .foregroundStyle(.tertiary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 420)
                .fixedSize(horizontal: false, vertical: true)

            Spacer()

            HStack {
                secondaryButton("Back") { panel = 0 }
                Spacer()
                primaryButton("Continue") { panel = 2 }
            }
        }
        .padding(24)
    }

    // MARK: - Panel 3: Notifications -----------------------------------------

    private var notificationsPanel: some View {
        VStack(spacing: 16) {
            sectionHeader(icon: "bell.badge", title: "Notifications")

            Text("ClaudeWatch can ping you when a session finishes, errors out, or crosses a cost threshold. Notifications are opt-in and routed through the standard macOS Notification Center.")
                .font(.system(size: 12.5))
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
                .frame(maxWidth: 460)
                .fixedSize(horizontal: false, vertical: true)

            notificationStatusBadge

            HStack(spacing: 12) {
                Button {
                    requestNotificationAuthorization()
                } label: {
                    Label("Request Permission", systemImage: "bell.fill")
                }
                .disabled(notificationStatus == .granted)

                Button("Check Again") {
                    refreshNotificationStatus()
                }
            }

            Spacer()

            HStack {
                secondaryButton("Back") { panel = 1 }
                Spacer()
                primaryButton("Continue") { panel = 3 }
            }
        }
        .padding(24)
        .onAppear { refreshNotificationStatus() }
    }

    private var notificationStatusBadge: some View {
        HStack(spacing: 8) {
            Image(systemName: notificationStatus.icon)
                .foregroundStyle(notificationStatus.color)
            Text(notificationStatus.label)
                .font(.system(size: 12.5, weight: .medium))
        }
        .padding(.vertical, 6)
        .padding(.horizontal, 12)
        .background(
            Capsule().fill(notificationStatus.color.opacity(0.12))
        )
    }

    // MARK: - Panel 4: Done --------------------------------------------------

    private var donePanel: some View {
        VStack(spacing: 18) {
            Image(systemName: "checkmark.seal.fill")
                .font(.system(size: 56))
                .foregroundStyle(
                    LinearGradient(
                        colors: [
                            Color(red: 0.18, green: 0.78, blue: 0.55),
                            Color(red: 0.07, green: 0.49, blue: 0.74),
                        ],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    )
                )

            Text("You're all set")
                .font(.system(size: 22, weight: .semibold))

            Text("ClaudeWatch lives in your menu bar — look for the ant icon. Click it to see active sessions; ⌘, opens settings.")
                .font(.system(size: 12.5))
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
                .frame(maxWidth: 440)
                .fixedSize(horizontal: false, vertical: true)

            Button {
                if let url = URL(string: "http://127.0.0.1:7788/") {
                    NSWorkspace.shared.open(url)
                }
            } label: {
                Label("Open Dashboard", systemImage: "safari")
            }

            Spacer()

            HStack {
                secondaryButton("Back") { panel = 2 }
                Spacer()
                primaryButton("Done") { onFinish() }
            }
        }
        .padding(24)
    }

    // MARK: - Building blocks ------------------------------------------------

    private var iconBadge: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 22, style: .continuous)
                .fill(
                    LinearGradient(
                        colors: [
                            Color(red: 0.18, green: 0.78, blue: 0.55),
                            Color(red: 0.07, green: 0.49, blue: 0.74),
                        ],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    )
                )
                .frame(width: 96, height: 96)
                .shadow(color: .black.opacity(0.18), radius: 10, x: 0, y: 4)
            Text("🐜")
                .font(.system(size: 56))
        }
    }

    private func sectionHeader(icon: String, title: String) -> some View {
        HStack(spacing: 10) {
            Image(systemName: icon)
                .font(.system(size: 22, weight: .medium))
                .foregroundStyle(.tint)
            Text(title)
                .font(.system(size: 20, weight: .semibold))
        }
        .padding(.top, 8)
    }

    private func primaryButton(_ title: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title)
                .frame(minWidth: 80)
        }
        .keyboardShortcut(.defaultAction)
        .controlSize(.large)
    }

    private func secondaryButton(_ title: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title)
                .frame(minWidth: 60)
        }
        .controlSize(.large)
    }

    private var pageIndicator: some View {
        HStack(spacing: 6) {
            ForEach(0..<4, id: \.self) { i in
                Circle()
                    .fill(i == panel ? Color.accentColor : Color.secondary.opacity(0.3))
                    .frame(width: 6, height: 6)
            }
        }
    }

    // MARK: - Actions --------------------------------------------------------

    /// Deep link to Privacy & Security → Automation. Works on macOS 13+.
    private func openAutomationSettings() {
        let url = URL(
            string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation"
        )!
        NSWorkspace.shared.open(url)
    }

    private func requestNotificationAuthorization() {
        UNUserNotificationCenter.current().requestAuthorization(
            options: [.alert, .sound]
        ) { granted, _ in
            Task { @MainActor in
                self.notificationStatus = granted ? .granted : .denied
            }
        }
    }

    private func refreshNotificationStatus() {
        UNUserNotificationCenter.current().getNotificationSettings { settings in
            // Convert the non-Sendable UNNotificationSettings into a plain
            // value before hopping onto the main actor (Swift 6 strict
            // concurrency would otherwise flag a data-race risk).
            let resolved: NotificationStatus
            switch settings.authorizationStatus {
            case .authorized, .provisional, .ephemeral:
                resolved = .granted
            case .denied:
                resolved = .denied
            case .notDetermined:
                resolved = .unknown
            @unknown default:
                resolved = .unknown
            }
            Task { @MainActor in
                self.notificationStatus = resolved
            }
        }
    }
}

// MARK: - Notification status helper -----------------------------------------

enum NotificationStatus: Equatable {
    case unknown
    case granted
    case denied

    var label: String {
        switch self {
        case .unknown: return "Not yet requested"
        case .granted: return "Granted"
        case .denied:  return "Denied — enable in System Settings → Notifications"
        }
    }

    var icon: String {
        switch self {
        case .unknown: return "questionmark.circle"
        case .granted: return "checkmark.circle.fill"
        case .denied:  return "xmark.octagon.fill"
        }
    }

    var color: Color {
        switch self {
        case .unknown: return .secondary
        case .granted: return .green
        case .denied:  return .red
        }
    }
}
