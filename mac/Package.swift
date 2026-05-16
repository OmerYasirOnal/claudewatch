// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "ClaudeWatchTray",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "ClaudeWatchTray", targets: ["ClaudeWatchTray"]),
    ],
    targets: [
        .executableTarget(
            name: "ClaudeWatchTray",
            path: "Sources/ClaudeWatchTray"
        ),
        .testTarget(
            name: "ClaudeWatchTrayTests",
            dependencies: ["ClaudeWatchTray"],
            path: "Tests/ClaudeWatchTrayTests"
        ),
    ]
)
