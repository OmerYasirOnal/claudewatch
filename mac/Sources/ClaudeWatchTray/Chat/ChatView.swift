import SwiftUI

/// SwiftUI view backing each chat window. One per PID; opened via
/// ChatWindowController.
///
/// Keybinding choices:
///   - Cmd+Enter → send. Enter alone inserts a newline.
/// Why: a Claude prompt is often multi-line (multi-paragraph specs, code
/// blocks), so an accidental Enter would constantly fire incomplete prompts.
/// Cmd+Enter is the convention shared with iMessage, Cursor, and the web
/// dashboard's existing chat panel.
struct ChatView: View {
    @ObservedObject var vm: ChatViewModel
    let onClose: () -> Void

    /// Drives focus into the composer TextEditor on window open so the user
    /// can start typing immediately without a mouse click.
    @FocusState private var inputFocused: Bool

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            transcript
            Divider()
            composer
            if let err = vm.lastError {
                errorBar(err)
            }
        }
        .frame(minWidth: 480, minHeight: 460)
        .onAppear {
            vm.start()
            // Defer focus by one runloop tick — SwiftUI's @FocusState can
            // race with the NSHostingController's initial layout pass and
            // get cleared again if we set it synchronously in onAppear.
            DispatchQueue.main.async { inputFocused = true }
        }
        .onDisappear { vm.stop() }
    }

    // MARK: - Header

    private var header: some View {
        HStack(spacing: 8) {
            Image(systemName: "bubble.left.and.bubble.right.fill")
                .foregroundStyle(.tint)
            VStack(alignment: .leading, spacing: 2) {
                Text(vm.projectName)
                    .font(.headline)
                    .lineLimit(1)
                HStack(spacing: 6) {
                    Text("PID \(vm.pid)")
                    if let model = vm.model {
                        Text("·")
                        Text(model)
                    }
                    Text("·")
                    connectionDot
                }
                .font(.caption)
                .foregroundStyle(.secondary)
            }
            Spacer()
            Button {
                onClose()
            } label: {
                Image(systemName: "xmark.circle.fill")
                    .foregroundStyle(.secondary)
            }
            .buttonStyle(.plain)
            .help("Close")
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
    }

    @ViewBuilder
    private var connectionDot: some View {
        switch vm.connectionState {
        case .connecting:
            Label("connecting", systemImage: "circle.dotted")
                .labelStyle(.titleAndIcon)
                .foregroundStyle(.yellow)
        case .connected:
            Label("live", systemImage: "circle.fill")
                .labelStyle(.titleAndIcon)
                .foregroundStyle(.green)
        case .disconnected:
            Label("offline", systemImage: "circle")
                .labelStyle(.titleAndIcon)
                .foregroundStyle(.red)
        }
    }

    // MARK: - Transcript

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 8) {
                    ForEach(vm.entries) { entry in
                        ChatEntryRow(entry: entry)
                            .id(entry.id)
                    }
                    // Sentinel to scroll to.
                    Color.clear.frame(height: 1).id("BOTTOM")
                }
                .padding(12)
            }
            .background(Color(nsColor: .textBackgroundColor).opacity(0.4))
            .onChange(of: vm.entries.count) { _, _ in
                withAnimation(.easeOut(duration: 0.12)) {
                    proxy.scrollTo("BOTTOM", anchor: .bottom)
                }
            }
        }
    }

    // MARK: - Composer

    private var composer: some View {
        VStack(spacing: 6) {
            if !vm.remoteEnabled {
                disabledNotice
            }
            HStack(alignment: .bottom, spacing: 8) {
                ZStack(alignment: .topLeading) {
                    if vm.draft.isEmpty {
                        Text(vm.remoteEnabled
                             ? "Type a message · Cmd+Return to send"
                             : "Remote control disabled — read-only mode")
                            .font(.body)
                            .foregroundStyle(.tertiary)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 4)
                            .allowsHitTesting(false)
                    }
                    TextEditor(text: $vm.draft)
                        .font(.body)
                        .scrollContentBackground(.hidden)
                        .frame(minHeight: 60, maxHeight: 140)
                        .padding(2)
                        .focused($inputFocused)
                        .disabled(!vm.remoteEnabled || vm.isSending)
                }
                .background(
                    RoundedRectangle(cornerRadius: 6)
                        .strokeBorder(.separator, lineWidth: 1)
                )
                Button {
                    Task { await vm.send() }
                } label: {
                    if vm.isSending {
                        HStack(spacing: 6) {
                            ProgressView().controlSize(.small)
                            Text("Sending…")
                        }
                    } else {
                        Label("Send", systemImage: "paperplane.fill")
                    }
                }
                // Cmd+Return fires the same action as clicking Send. SwiftUI
                // routes this even while the TextEditor has focus because the
                // shortcut lives on a Button at view-scope (not a child of
                // the TextEditor's responder chain).
                .keyboardShortcut(.return, modifiers: [.command])
                .buttonStyle(.borderedProminent)
                .help("Send (⌘↩)")
                .disabled(!vm.remoteEnabled
                          || vm.isSending
                          || vm.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
    }

    private var disabledNotice: some View {
        HStack(spacing: 6) {
            Image(systemName: "lock.fill")
                .foregroundStyle(.orange)
            Text("Remote control is disabled. Enable it in Settings → Remote Control to send messages.")
                .font(.caption)
                .foregroundStyle(.secondary)
            Spacer()
        }
    }

    private func errorBar(_ msg: String) -> some View {
        HStack(spacing: 6) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(.red)
            Text(msg)
                .font(.caption)
                .foregroundStyle(.red)
                .lineLimit(2)
            Spacer()
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
        .background(Color.red.opacity(0.08))
    }
}

/// One row in the transcript. Roles get distinct backgrounds + icons so the
/// reader can scan structure quickly without reading every word.
private struct ChatEntryRow: View {
    let entry: ChatEntry

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            roleBadge
            VStack(alignment: .leading, spacing: 2) {
                roleLabel
                bodyText
                    .font(entry.role == .toolUse ? .system(.caption, design: .monospaced) : .body)
                    .textSelection(.enabled)
                    .foregroundStyle(entry.role == .redacted ? .secondary : .primary)
                    .italic(entry.role == .redacted)
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 6)
        .padding(.horizontal, 8)
        .background(background)
        .clipShape(RoundedRectangle(cornerRadius: 6))
    }

    /// Assistant replies are rendered as Markdown so backticks, bold, and
    /// lists look right in the panel. Everything else is plain text — in
    /// particular, user-typed messages must NOT be markdown-parsed because
    /// a stray `*` or `_` would silently disappear, and tool/system entries
    /// are diagnostic strings where Markdown would just add noise.
    ///
    /// Issue #129: assistant text is attacker-controllable via prompt
    /// injection / malicious MCP servers. The previous implementation used
    /// `Text(.init(entry.text))` which would happily turn a markdown link
    /// like `[click](javascript:alert(1))` or `[file](file:///etc/passwd)`
    /// into a clickable Link that NSWorkspace would honor. We now parse the
    /// markdown into an `AttributedString` ourselves and strip any `.link`
    /// run whose URL scheme isn't in the {http, https} allowlist.
    @ViewBuilder
    private var bodyText: some View {
        if entry.role == .assistant {
            Text(ChatMarkdown.sanitizedAttributedString(from: entry.text))
        } else {
            Text(entry.text)
        }
    }

    @ViewBuilder
    private var roleBadge: some View {
        Image(systemName: badgeIcon)
            .foregroundStyle(badgeColor)
            .frame(width: 16)
            .padding(.top, 2)
    }

    @ViewBuilder
    private var roleLabel: some View {
        Text(roleTitle)
            .font(.caption)
            .foregroundStyle(.secondary)
    }

    private var roleTitle: String {
        switch entry.role {
        case .user: return "You"
        case .assistant: return "Claude"
        case .toolUse: return "Tool · \(entry.toolName ?? "?")"
        case .toolResult: return "Tool result"
        case .system: return "System"
        case .redacted: return "Hidden"
        }
    }

    private var badgeIcon: String {
        switch entry.role {
        case .user: return "person.fill"
        case .assistant: return "sparkles"
        case .toolUse: return "wrench.and.screwdriver.fill"
        case .toolResult: return "doc.text"
        case .system: return "gearshape.fill"
        case .redacted: return "eye.slash.fill"
        }
    }

    private var badgeColor: Color {
        switch entry.role {
        case .user: return .blue
        case .assistant: return .purple
        case .toolUse: return .orange
        case .toolResult: return .gray
        case .system: return .secondary
        case .redacted: return .secondary
        }
    }

    private var background: Color {
        switch entry.role {
        case .user: return Color.blue.opacity(0.06)
        case .assistant: return Color.purple.opacity(0.05)
        case .toolUse: return Color.orange.opacity(0.05)
        case .toolResult: return Color.gray.opacity(0.05)
        case .system, .redacted: return Color.clear
        }
    }
}
