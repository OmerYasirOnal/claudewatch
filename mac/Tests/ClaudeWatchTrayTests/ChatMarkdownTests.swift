import XCTest
@testable import ClaudeWatchTray

/// Pins the safety contract on `ChatMarkdown.sanitizedAttributedString`:
///   - http:// and https:// links survive intact as clickable `.link` runs.
///   - javascript:, file://, custom URL schemes are stripped — the run
///     remains visible as text but has NO `.link` attribute, so SwiftUI's
///     Text won't render it as a clickable link and NSWorkspace can't be
///     handed an attacker-controlled URL on a click.
///   - The display text of stripped links is preserved verbatim, so the
///     user still sees what the assistant said.
///   - Plain (non-link) markdown formatting (bold, code, etc.) is left
///     alone — we only sanitize URLs.
///
/// Issue #129. Driving `stripDisallowedLinks(in:)` directly avoids any
/// dependency on the underlying markdown parser's exact behavior.
final class ChatMarkdownTests: XCTestCase {

    // MARK: - allowlist semantics

    func testAllowedSchemes() {
        XCTAssertTrue(ChatMarkdown.isAllowed(url: URL(string: "https://example.com")!))
        XCTAssertTrue(ChatMarkdown.isAllowed(url: URL(string: "http://example.com")!))
        // Scheme matching is case-insensitive per RFC 3986.
        XCTAssertTrue(ChatMarkdown.isAllowed(url: URL(string: "HTTPS://example.com")!))
    }

    func testDisallowedSchemes() {
        XCTAssertFalse(ChatMarkdown.isAllowed(url: URL(string: "javascript:alert(1)")!))
        XCTAssertFalse(ChatMarkdown.isAllowed(url: URL(string: "file:///etc/passwd")!))
        // Even mailto is disallowed by default — see the rationale in
        // ChatMarkdown.swift.
        XCTAssertFalse(ChatMarkdown.isAllowed(url: URL(string: "mailto:victim@example.com")!))
        XCTAssertFalse(ChatMarkdown.isAllowed(url: URL(string: "slack://open?team=evil")!))
        XCTAssertFalse(ChatMarkdown.isAllowed(url: URL(string: "data:text/html,<script>alert(1)</script>")!))
    }

    // MARK: - stripping behavior on AttributedString

    func testStripsJavascriptLink() throws {
        // Build an AttributedString with a single .link run pointing at a
        // javascript: URL — bypasses the markdown parser so we test the
        // sanitizer's behavior in isolation.
        var input = AttributedString("click me")
        input.link = URL(string: "javascript:alert(1)")!

        let sanitized = ChatMarkdown.stripDisallowedLinks(in: input)

        // Display text preserved.
        XCTAssertEqual(String(sanitized.characters), "click me")
        // No remaining .link attribute anywhere in the result.
        for run in sanitized.runs {
            XCTAssertNil(run.link,
                         "javascript: link must be stripped (found \(String(describing: run.link)))")
        }
    }

    func testStripsFileLink() throws {
        var input = AttributedString("payslip.pdf")
        input.link = URL(string: "file:///etc/passwd")!

        let sanitized = ChatMarkdown.stripDisallowedLinks(in: input)

        XCTAssertEqual(String(sanitized.characters), "payslip.pdf")
        for run in sanitized.runs {
            XCTAssertNil(run.link, "file:// link must be stripped")
        }
    }

    func testPreservesHttpsLink() throws {
        let expected = URL(string: "https://anthropic.com")!
        var input = AttributedString("docs")
        input.link = expected

        let sanitized = ChatMarkdown.stripDisallowedLinks(in: input)

        // The link must still be present and unchanged.
        let links = sanitized.runs.compactMap { $0.link }
        XCTAssertEqual(links, [expected],
                       "https links must survive sanitization unchanged")
    }

    // MARK: - end-to-end through the markdown parser

    func testMarkdownJavascriptLinkProducesNoLinkRun() {
        // The whole point of the fix: an assistant message containing
        // `[hi](javascript:alert(1))` must produce an AttributedString with
        // NO `.link` attribute, ensuring SwiftUI cannot render it as a
        // clickable URL.
        let markdown = "[hi](javascript:alert(1))"
        let attributed = ChatMarkdown.sanitizedAttributedString(from: markdown)

        let linkURLs = attributed.runs.compactMap { $0.link }
        XCTAssertTrue(linkURLs.isEmpty,
                      "javascript: markdown link must not produce any .link run (got \(linkURLs))")
        // The label is still visible to the user — we don't drop the text.
        XCTAssertTrue(String(attributed.characters).contains("hi"),
                      "stripped link must keep its label text visible")
    }

    func testMarkdownFileLinkProducesNoLinkRun() {
        let markdown = "[innocent](file:///Applications/Calculator.app)"
        let attributed = ChatMarkdown.sanitizedAttributedString(from: markdown)

        for run in attributed.runs {
            XCTAssertNil(run.link, "file:// markdown link must be stripped end-to-end")
        }
        XCTAssertTrue(String(attributed.characters).contains("innocent"))
    }

    func testMarkdownHttpsLinkSurvives() {
        let markdown = "[Anthropic](https://anthropic.com)"
        let attributed = ChatMarkdown.sanitizedAttributedString(from: markdown)

        let linkURLs = attributed.runs.compactMap { $0.link }
        XCTAssertEqual(linkURLs.map(\.absoluteString), ["https://anthropic.com"],
                       "https markdown links must survive sanitization")
    }

    func testPlainTextIsUntouched() {
        // No links → output text should match the input verbatim (modulo
        // any whitespace/format normalization the markdown parser does;
        // we just assert the user-visible characters survive).
        let attributed = ChatMarkdown.sanitizedAttributedString(from: "hello **world**")
        let chars = String(attributed.characters)
        XCTAssertTrue(chars.contains("hello"))
        XCTAssertTrue(chars.contains("world"))
        // No link runs at all.
        for run in attributed.runs {
            XCTAssertNil(run.link)
        }
    }
}
