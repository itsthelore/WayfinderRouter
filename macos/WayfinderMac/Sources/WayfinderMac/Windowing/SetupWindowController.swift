import AppKit
import Combine
import SwiftUI

@MainActor
final class SetupWindowController: NSObject, NSWindowDelegate {
    private let state: SetupState
    private var window: NSWindow!
    private var stepCancellable: AnyCancellable?

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
        window.setContentSize(Self.preferredContentSize(for: state.step))
        window.contentMinSize = NSSize(width: 520, height: 300)
        window.isReleasedWhenClosed = false
        window.center()
        window.delegate = self
        self.window = window
        stepCancellable = state.$step
            .removeDuplicates()
            .dropFirst()
            .sink { [weak self] step in
                self?.resize(for: step)
            }
    }

    static func preferredContentSize(for step: SetupStep) -> NSSize {
        let height: CGFloat
        switch step {
        case .chooseRouting:
            height = 500
        case .credentials, .configure, .result, .requirements, .bundledHelperInvalid, .serviceRepair:
            height = 400
        case .checking, .welcome, .existingConfiguration:
            height = 340
        }
        return NSSize(width: 560, height: height)
    }

    private func resize(for step: SetupStep) {
        let targetSize = Self.preferredContentSize(for: step)
        guard window.contentView?.frame.size != targetSize else { return }
        window.setContentSize(targetSize)
        window.center()
    }

    private func present() {
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func reassessAndShow() {
        present()
        Task { await state.assess() }
    }

    func assessAndShowIfNeeded() {
        Task {
            await state.assess()
            let deferred = UserDefaults.standard.bool(forKey: "Wayfinder.Setup.Deferred")
            if state.assessment.isIncomplete && !deferred { present() }
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
