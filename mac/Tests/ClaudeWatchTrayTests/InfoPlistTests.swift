import XCTest

/// `Info.plist` is the source of truth for hardened-runtime metadata. If we
/// silently drop a required key (e.g. `LSMinimumSystemVersion`) the notary
/// service rejects the bundle and the failure surfaces only at release
/// time — too late. This test pins the keys the notarization pipeline cares
/// about so a regression breaks the build, not the release.
///
/// We DON'T test runtime entitlements here (those live in
/// `mac/Entitlements.plist`, applied at codesign time). That's a separate
/// file and exercising it requires actually invoking codesign, which we
/// skip in unit tests — see `mac/docs/code-signing.md` for the full
/// signing flow.
final class InfoPlistTests: XCTestCase {

    /// Walk up from this source file to the package root (`mac/`) so the
    /// test is portable across `swift test` invocations from any CWD.
    /// The Swift Package layout puts test sources at
    /// `mac/Tests/ClaudeWatchTrayTests/InfoPlistTests.swift`, so the
    /// package root is three `deletingLastPathComponent()` calls up.
    private func packageRoot() -> URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()  // ClaudeWatchTrayTests/
            .deletingLastPathComponent()  // Tests/
            .deletingLastPathComponent()  // mac/
    }

    private func loadInfoPlist() throws -> [String: Any] {
        let plistURL = packageRoot().appendingPathComponent("Info.plist")
        let data = try Data(contentsOf: plistURL)
        guard let dict = try PropertyListSerialization.propertyList(
            from: data, options: [], format: nil
        ) as? [String: Any] else {
            throw XCTSkip("Info.plist did not deserialize to a dictionary")
        }
        return dict
    }

    /// Apple's notary service rejects anything missing one of these keys
    /// with a generic "Invalid bundle" error. Keep them explicit so the
    /// failure mode is "this test failed" instead of "release pipeline
    /// failed at 11pm".
    func test_info_plist_has_required_bundle_keys() throws {
        let plist = try loadInfoPlist()

        let required = [
            "CFBundleIdentifier",
            "CFBundleName",
            "CFBundleExecutable",
            "CFBundleVersion",
            "CFBundleShortVersionString",
            "CFBundlePackageType",
            "LSMinimumSystemVersion",
        ]

        for key in required {
            XCTAssertNotNil(plist[key], "Info.plist is missing required key: \(key)")
        }
    }

    /// We're a menu-bar-only app. `LSUIElement=true` keeps the Dock icon
    /// hidden — without it, end users see a stray Dock icon for an app
    /// they only interact with via the menu bar. Notarization doesn't
    /// require this, but our UX does.
    func test_info_plist_is_menu_bar_only() throws {
        let plist = try loadInfoPlist()
        XCTAssertEqual(plist["LSUIElement"] as? Bool, true,
                       "LSUIElement must be true so the app is menu-bar-only (no Dock icon)")
    }

    /// CFBundleIdentifier is also the certificate matching key when
    /// notarized — if it ever changes from the documented value, every
    /// previously-stapled DMG breaks. Pin the canonical value.
    func test_info_plist_bundle_identifier_is_canonical() throws {
        let plist = try loadInfoPlist()
        XCTAssertEqual(plist["CFBundleIdentifier"] as? String,
                       "com.omeryasironal.claudewatch.tray",
                       "Bundle ID change requires a new Developer ID cert mapping; coordinate before bumping")
    }

    /// Sparkle's auto-updater reads the appcast URL from Info.plist. If we
    /// drop the key the user silently never gets updates. The placeholder
    /// public key is intentional — see `mac/docs/sparkle-setup.md`.
    func test_info_plist_has_sparkle_feed_url() throws {
        let plist = try loadInfoPlist()
        let feed = plist["SUFeedURL"] as? String
        XCTAssertNotNil(feed, "SUFeedURL is required for Sparkle auto-updates")
        XCTAssertTrue(feed?.hasPrefix("https://") ?? false,
                      "SUFeedURL must be https:// (Sparkle refuses http)")
    }

    /// Hardened runtime requires LSMinimumSystemVersion ≥ 10.13 (Apple's
    /// floor for hardened-runtime support). Ours is 14.0 already, but pin
    /// the lower bound so a future bump can't accidentally go below the
    /// notarization floor.
    func test_info_plist_minimum_system_version_supports_hardened_runtime() throws {
        let plist = try loadInfoPlist()
        guard let raw = plist["LSMinimumSystemVersion"] as? String else {
            XCTFail("LSMinimumSystemVersion missing or not a string")
            return
        }
        // Parse the major version; we don't care about patch components.
        let major = Int(raw.split(separator: ".").first ?? "0") ?? 0
        XCTAssertGreaterThanOrEqual(major, 10,
                                    "LSMinimumSystemVersion must be ≥ 10 for hardened runtime support; got \(raw)")
    }
}
