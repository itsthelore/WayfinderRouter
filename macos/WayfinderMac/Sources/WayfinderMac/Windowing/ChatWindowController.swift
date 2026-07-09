import AppKit
import SwiftUI

@MainActor
final class ChatWindowController {
    private let window: NSWindow

    init(appState: AppState) {
        let rootView = WayfinderChatWindow()
            .environmentObject(appState)
        let hostingController = NSHostingController(rootView: rootView)
        let window = NSWindow(contentViewController: hostingController)
        window.title = "Wayfinder Chat"
        window.styleMask = [.titled, .closable, .miniaturizable, .resizable]
        window.setContentSize(NSSize(width: 620, height: 760))
        window.minSize = NSSize(width: 520, height: 700)
        window.isReleasedWhenClosed = false
        self.window = window
    }

    func show() {
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }
}
