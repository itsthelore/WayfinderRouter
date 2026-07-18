import AppKit
import SwiftUI

@MainActor
final class SettingsWindowController {
    private let window: NSWindow
    private let navigation: SettingsWindowNavigation

    init(appState: AppState) {
        let navigation = SettingsWindowNavigation()
        let rootView = WayfinderSettingsWindow(appState: appState, navigation: navigation)
            .environmentObject(appState)
        let hostingController = NSHostingController(rootView: rootView)
        let window = NSWindow(contentViewController: hostingController)
        window.title = "Settings"
        window.styleMask = [.titled, .closable, .miniaturizable, .resizable]
        window.setContentSize(NSSize(width: 700, height: 520))
        window.contentMinSize = NSSize(width: 620, height: 460)
        window.isReleasedWhenClosed = false
        let frameName = NSWindow.FrameAutosaveName("Wayfinder.Settings")
        if !window.setFrameUsingName(frameName) {
            window.center()
        }
        window.setFrameAutosaveName(frameName)
        self.window = window
        self.navigation = navigation
    }

    func show(section: SettingsSection = .gateway) {
        navigation.select(section)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }
}
