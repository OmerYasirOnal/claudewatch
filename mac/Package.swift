// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "ClaudeWatchTray",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "ClaudeWatchTray", targets: ["ClaudeWatchTray"]),
    ],
    dependencies: [
        // Sparkle 2 — software-update framework for macOS apps.
        // `from: 2.6.0` lets SwiftPM pick the latest 2.x (currently 2.9.2),
        // which is the conventional way to pin against a stable major.
        // Supports macOS 10.13+, EdDSA signatures, sandbox-friendly XPC.
        // See mac/docs/sparkle-setup.md for the release-signing pipeline.
        .package(url: "https://github.com/sparkle-project/Sparkle", from: "2.6.0"),
    ],
    targets: [
        .executableTarget(
            name: "ClaudeWatchTray",
            dependencies: [
                .product(name: "Sparkle", package: "Sparkle"),
            ],
            path: "Sources/ClaudeWatchTray"
        ),
        .testTarget(
            name: "ClaudeWatchTrayTests",
            dependencies: ["ClaudeWatchTray"],
            path: "Tests/ClaudeWatchTrayTests"
        ),
    ]
)
