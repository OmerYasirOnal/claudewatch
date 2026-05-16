import SwiftUI

struct SessionRow: View {
    let sess: Session
    let onFocus: () -> Void
    let onHalt: () -> Void
    let onChat: () -> Void

    private var statusColor: Color {
        if sess.isInFlight { return .green }
        switch sess.status {
        case "working": return .green
        case "waiting": return .yellow
        case "idle": return .gray
        default: return .gray
        }
    }

    private var costStr: String {
        let c = sess.costEstimate
        if c <= 0 { return "—" }
        return String(format: "$%.2f", c)
    }

    private var durationStr: String {
        let s = sess.durationSeconds
        if s < 60 { return "\(s)s" }
        if s < 3600 {
            let m = s / 60
            return "\(m)m"
        }
        let h = s / 3600
        let m = (s % 3600) / 60
        return "\(h)h \(m)m"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Circle()
                    .fill(statusColor)
                    .frame(width: 8, height: 8)
                Text(sess.projectName)
                    .font(.system(.body, design: .default).weight(.medium))
                    .lineLimit(1)
                    .truncationMode(.middle)
                Spacer(minLength: 4)
                Text(costStr)
                    .font(.system(.caption, design: .monospaced))
                    .foregroundStyle(.secondary)
            }
            HStack(spacing: 6) {
                Text("PID \(sess.pid)")
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                if let model = sess.model {
                    Text("·")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                    Text(model)
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                        .lineLimit(1)
                }
                Spacer()
                Text(durationStr)
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            }
            if let task = sess.currentTaskSubject, !task.isEmpty {
                Text("▸ \(task)")
                    .font(.caption)
                    .lineLimit(1)
                    .foregroundStyle(.secondary)
            }
            HStack(spacing: 6) {
                Button("Focus", action: onFocus)
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                    .disabled(sess.locationType == "headless")
                Button(role: .destructive, action: onHalt) { Text("Halt") }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                Button(action: onChat) {
                    Label("Chat", systemImage: "bubble.left.and.bubble.right")
                }
                .labelStyle(.iconOnly)
                .buttonStyle(.bordered)
                .controlSize(.small)
                .help("Open chat for this session")
                Spacer()
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .contentShape(Rectangle())
    }
}
