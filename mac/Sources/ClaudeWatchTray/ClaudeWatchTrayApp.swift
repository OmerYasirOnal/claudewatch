import SwiftUI

@main
struct ClaudeWatchTrayApp: App {
    @StateObject private var vm = AppViewModel()

    var body: some Scene {
        MenuBarExtra {
            MenuBarContent(vm: vm)
        } label: {
            HStack(spacing: 4) {
                Image(systemName: "ant.fill")
                if !vm.menuBarLabel.isEmpty {
                    Text(vm.menuBarLabel)
                }
            }
        }
        .menuBarExtraStyle(.window)
    }
}
