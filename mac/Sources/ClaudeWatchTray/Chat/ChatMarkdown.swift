import Foundation

/// Sanitizing markdown renderer for assistant chat output.
///
/// Issue #129: assistant text is, by trust model, attacker-controllable —
/// it's whatever a malicious MCP server / tool result / injected prompt
/// managed to make Claude emit. SwiftUI's `Text(.init(markdownString))`
/// auto-parses markdown via `LocalizedStringKey`, which means
/// `[innocent](file:///Applications/Calculator.app)` or
/// `[click](slack://...)` become clickable links that NSWorkspace will
/// happily honor on a click, launching arbitrary registered URL handlers.
///
/// This helper parses the markdown into an `AttributedString`, walks the
/// runs, and strips any `.link` attribute whose URL scheme is not in the
/// {http, https} allowlist. The link's display text is preserved verbatim
/// — only the click-to-execute target is removed. Plain (un-decorated)
/// text in the message is left untouched, as is all other markdown
/// formatting (bold, code, lists, etc.).
enum ChatMarkdown {

    /// URL schemes we allow to remain as clickable links. Everything else
    /// (file://, javascript:, custom app schemes like slack://, mailto:,
    /// data:, etc.) is stripped. `mailto:` is intentionally NOT here:
    /// while benign on most systems, it can still hand off to arbitrary
    /// configured mail clients and isn't worth the surface area for a
    /// monitoring panel. If we ever need it, add it explicitly.
    private static let allowedSchemes: Set<String> = ["http", "https"]

    /// Parse `markdown` and return an `AttributedString` safe to pass to
    /// SwiftUI's `Text(_: AttributedString)`. Any `.link` runs whose URL
    /// scheme is not allow-listed have their `.link` attribute removed.
    ///
    /// If markdown parsing itself fails (very unusual — `AttributedString`
    /// is permissive), the raw input is returned as a plain
    /// `AttributedString` so the user still sees the text rather than an
    /// empty bubble.
    static func sanitizedAttributedString(from markdown: String) -> AttributedString {
        let parsed: AttributedString
        do {
            // .full = parse the entire string as markdown, including
            // multi-line constructs. Matches what `Text(.init(...))` does.
            parsed = try AttributedString(
                markdown: markdown,
                options: AttributedString.MarkdownParsingOptions(
                    interpretedSyntax: .full
                )
            )
        } catch {
            return AttributedString(markdown)
        }
        return stripDisallowedLinks(in: parsed)
    }

    /// Walks the runs of `attributed` and removes the `.link` attribute on
    /// any run whose URL scheme is not in `allowedSchemes`. Returns the
    /// sanitized copy; the input is not mutated.
    ///
    /// Internal so tests can drive it directly without round-tripping
    /// through a markdown parser.
    static func stripDisallowedLinks(in attributed: AttributedString) -> AttributedString {
        var copy = attributed
        // Iterating `copy.runs` gives ranges into `copy` we can mutate via
        // subscript. We collect the ranges-to-strip first to avoid mutating
        // the run collection while iterating it.
        var rangesToStrip: [Range<AttributedString.Index>] = []
        for run in copy.runs {
            guard let link = run.link else { continue }
            if !isAllowed(url: link) {
                rangesToStrip.append(run.range)
            }
        }
        for range in rangesToStrip {
            copy[range].link = nil
        }
        return copy
    }

    /// True iff `url` has a scheme that's safe to render as a clickable
    /// link. Schemes are compared case-insensitively (per RFC 3986 §3.1
    /// schemes are case-insensitive). A URL with no scheme — which
    /// shouldn't normally appear on a `.link` attribute but we handle
    /// defensively — is treated as disallowed.
    static func isAllowed(url: URL) -> Bool {
        guard let scheme = url.scheme?.lowercased() else { return false }
        return allowedSchemes.contains(scheme)
    }
}
