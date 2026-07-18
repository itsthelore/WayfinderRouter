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
        window.setContentSize(NSSize(width: 1_180, height: 760))
        window.contentMinSize = NSSize(width: 1_040, height: 620)
        window.isReleasedWhenClosed = false
        let frameName = NSWindow.FrameAutosaveName("Wayfinder.Chat")
        if !window.setFrameUsingName(frameName) {
            window.center()
        }
        window.setFrameAutosaveName(frameName)
        self.window = window
    }

    func show() {
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }
}

extension ChatWindowController: ChatWindowPresenting {}
