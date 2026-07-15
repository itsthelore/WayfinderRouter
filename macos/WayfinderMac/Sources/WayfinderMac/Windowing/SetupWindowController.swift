import AppKit
import SwiftUI

@MainActor
final class SetupWindowController: NSObject, NSWindowDelegate {
    private let state: SetupState
    private var window: NSWindow!

    override convenience init() {
        self.init(state: SetupState())
    }

    init(state: SetupState) {
        self.state = state
        super.init()
        let root = SetupAssistantView(state: state) { [weak self] in self?.window.orderOut(nil) }
        let window = NSWindow(contentViewController: NSHostingController(rootView: root))
        window.title = "Set up Wayfinder"
        window.styleMask = [.titled, .closable, .resizable]
        window.setContentSize(NSSize(width: 560, height: 460))
        window.contentMinSize = NSSize(width: 520, height: 420)
        window.isReleasedWhenClosed = false
        window.center()
        window.delegate = self
        self.window = window
    }

    func show() {
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        Task { await state.assess() }
    }

    func assessAndShowIfNeeded() {
        Task {
            await state.assess()
            let deferred = UserDefaults.standard.bool(forKey: "Wayfinder.Setup.Deferred")
            if state.assessment.isIncomplete && !deferred { show() }
        }
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        guard state.isMutating else { return true }
        let alert = NSAlert()
        alert.messageText = "Stop setup?"
        alert.informativeText = "Completed steps remain applied. Wayfinder will reassess the actual state next time."
        alert.addButton(withTitle: "Keep Running")
        alert.addButton(withTitle: "Stop Setup")
        if alert.runModal() == .alertSecondButtonReturn { state.cancel(); return true }
        return false
    }
}
