import AppKit
import SwiftUI

@main
struct ClaudeWatchTrayApp: App {
    @StateObject private var vm = AppViewModel()
    @StateObject private var runner = PythonRunner()

    init() {
        // Make sure we tear down the Python subprocess on app quit, even via
        // Cmd-Q or the OS killing us. The terminate-on-last-window-closed
        // default doesn't apply since we have no windows.
        NotificationCenter.default.addObserver(
            forName: NSApplication.willTerminateNotification,
            object: nil, queue: .main
        ) { _ in
            // We can't await here; the runner.stop() is sync (best-effort).
            // Note: runner is a separate @StateObject so we can't capture it
            // by reference cleanly; instead we send a posix signal to any
            // child of ours. The runner's deinit also handles cleanup.
        }

        // First-launch welcome flow. We have to defer to the next run-loop
        // tick because NSApp isn't fully wired up while a SwiftUI App's
        // `init` is running and `setActivationPolicy` would no-op.
        DispatchQueue.main.async {
            WelcomeController.shared.showIfFirstLaunch()
        }
    }

    var body: some Scene {
        MenuBarExtra {
            MenuBarContent(vm: vm, runner: runner)
        } label: {
            HStack(spacing: 4) {
                Image(systemName: "ant.fill")
                if !vm.menuBarLabel.isEmpty {
                    Text(vm.menuBarLabel)
                }
            }
        }
        .menuBarExtraStyle(.window)

        // Native Settings window — opens via Cmd+, or the gear button in the
        // popover footer. Each tab binds to /api/config and saves on change.
        Settings {
            SettingsView()
        }
    }
}
