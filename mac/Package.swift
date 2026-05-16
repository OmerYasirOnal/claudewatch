// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "ClaudeWatchTray",
    platforms: [.macOS(.v13)],
    products: [
        .executable(name: "ClaudeWatchTray", targets: ["ClaudeWatchTray"]),
    ],
    targets: [
        .executableTarget(
            name: "ClaudeWatchTray",
            path: "Sources/ClaudeWatchTray"
        ),
    ]
)
